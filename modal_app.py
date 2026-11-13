"""Modal app: CPU inference endpoint (3-model ensemble) for NSFW image moderation + Streamlit test UI."""
import hmac
import json
import os
import subprocess
import time

import modal

import config

app = modal.App(config.APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        # Exact versions = reproducible builds (these are the versions that were tested).
        "torch==2.5.1",
        "transformers==4.46.3",
        "huggingface_hub==0.36.2",
        "timm==1.0.31",
        "numpy==2.4.6",
        "pillow==13.0.0",
        "safetensors==0.8.0",
        "fastapi[standard]==0.162.6",
        "python-multipart==0.0.33",
        extra_index_url="https://download.pytorch.org/whl/cpu",
    )
    .env({"HF_HOME": config.CACHE_DIR})
    .add_local_python_source("config", "classifier", "schemas")
)

volume = modal.Volume.from_name(config.VOLUME_NAME, create_if_missing=True)
secret = modal.Secret.from_name(config.SECRET_NAME)


@app.function(image=image, volumes={config.CACHE_DIR: volume}, timeout=600)
def download_model():
    """One-time: download model into the Volume."""
    from classifier import NSFWClassifier

    NSFWClassifier()
    volume.commit()
    print("Model cached in volume.")


@app.cls(
    image=image,
    volumes={config.CACHE_DIR: volume},
    secrets=[secret],
    timeout=config.REQUEST_TIMEOUT_S,
    scaledown_window=60,   # container stops 60 s after the last request (saves credits)
    cpu=1,
    memory=2048,           # measured peak is ~750 MiB with all 3 models loaded
    max_containers=3,      # hard cap on parallel containers -> caps the maximum cost
)
class Moderator:
    @modal.enter()
    def load(self):
        # Models are already in the Volume (see download_model). Offline mode means the service
        # never touches the network at startup, so it cannot break because of Hugging Face.
        os.environ["HF_HUB_OFFLINE"] = "1"
        from classifier import NSFWClassifier

        t0 = time.perf_counter()
        self.clf = NSFWClassifier()
        print(json.dumps({"event": "models_loaded", "seconds": round(time.perf_counter() - t0, 1)}))

    @modal.method()
    def predict_bytes(self, data: bytes) -> dict:
        return self.clf.predict(data)

    @modal.asgi_app()
    def web(self):
        from fastapi import FastAPI, File, Header, HTTPException, UploadFile

        from classifier import ImageError
        from schemas import ModerationResponse

        api = FastAPI(title="Image Moderation")

        @api.get("/health")
        def health():
            return {"status": "ok"}

        # Plain `def` (not async): FastAPI runs it in a worker thread, so the blocking model
        # call never freezes the event loop (e.g. /health keeps answering during inference).
        @api.post("/moderate", response_model=ModerationResponse)
        def moderate(
            file: UploadFile = File(...),
            authorization: str = Header(default=""),
        ):
            expected = os.environ.get("MODERATION_API_KEY", "")
            token = authorization.removeprefix("Bearer ").strip()
            if not expected or not hmac.compare_digest(token, expected):
                print(json.dumps({"event": "unauthorized"}))
                raise HTTPException(status_code=401, detail="Unauthorized")

            data = file.file.read(config.MAX_IMAGE_BYTES + 1)
            try:
                result = self.clf.predict(data)
            except ImageError as e:
                print(json.dumps({"event": "bad_image", "reason": str(e), "bytes": len(data)}))
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:  # never leak internals; the backend will keep the image private
                print(json.dumps({"event": "error", "type": type(e).__name__}))
                raise HTTPException(status_code=500, detail="Internal error")

            # Log scores only (no image content, no filename) for monitoring and calibration.
            print(json.dumps({
                "event": "moderated", "decision": result["decision"], "nsfw": result["nsfw_score"],
                "suggestive": result["suggestive_score"], "ms": result["latency_ms"], "bytes": len(data),
            }))
            return result

        return api


@app.local_entrypoint()
def main(path: str = ""):
    """modal run modal_app.py -> downloads model; --path img.jpg -> also tests it."""
    download_model.remote()
    if path:
        with open(path, "rb") as f:
            print(Moderator().predict_bytes.remote(f.read()))


# ---------------- Streamlit test UI ----------------
ui_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("streamlit==1.40.2", "pillow", "pandas")
    .add_local_python_source("config")
    .add_local_file("ui_app.py", "/root/ui_app.py")
)
ui_secret = modal.Secret.from_name("ui-auth")


@app.function(image=ui_image, secrets=[ui_secret], scaledown_window=120, timeout=3600, max_containers=2)
@modal.concurrent(max_inputs=10)
@modal.web_server(8501, startup_timeout=60)
def ui():
    cmd = (
        "streamlit run /root/ui_app.py --server.port 8501 --server.address 0.0.0.0 "
        "--server.headless true --server.enableCORS false --server.enableXsrfProtection false"
    )
    subprocess.Popen(cmd, shell=True)
