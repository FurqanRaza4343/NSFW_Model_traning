"""Modal app: GPU-less CPU inference endpoint for NSFW image moderation."""
import hmac
import os
import subprocess

import modal

import config

app = modal.App(config.APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch==2.5.1",
        "transformers==4.46.3",
        "pillow",
        "safetensors",
        "timm",
        "fastapi[standard]",
        "python-multipart",
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
    scaledown_window=60,
    cpu=1,
    memory=2048,
)
class Moderator:
    @modal.enter()
    def load(self):
        from classifier import NSFWClassifier

        self.clf = NSFWClassifier()

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

        @api.post("/moderate", response_model=ModerationResponse)
        async def moderate(
            file: UploadFile = File(...),
            authorization: str = Header(default=""),
        ):
            expected = os.environ.get("MODERATION_API_KEY", "")
            token = authorization.removeprefix("Bearer ").strip()
            if not expected or not hmac.compare_digest(token, expected):
                raise HTTPException(status_code=401, detail="Unauthorized")

            data = await file.read(config.MAX_IMAGE_BYTES + 1)
            try:
                return self.clf.predict(data)
            except ImageError as e:
                raise HTTPException(status_code=400, detail=str(e))

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


@app.function(image=ui_image, secrets=[ui_secret], scaledown_window=120, timeout=3600)
@modal.concurrent(max_inputs=10)
@modal.web_server(8501, startup_timeout=60)
def ui():
    cmd = (
        "streamlit run /root/ui_app.py --server.port 8501 --server.address 0.0.0.0 "
        "--server.headless true --server.enableCORS false --server.enableXsrfProtection false"
    )
    subprocess.Popen(cmd, shell=True)
