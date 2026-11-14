"""Modal app: CPU inference endpoint (3-model ensemble) for NSFW image moderation,
customer API keys with rate limits, and a Streamlit test / onboarding UI."""
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
    .add_local_python_source("config", "classifier", "schemas", "keys")
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
        from fastapi.middleware.cors import CORSMiddleware

        import keys as keylib
        from classifier import ImageError
        from schemas import ModerationResponse

        api = FastAPI(
            title="Image Moderation API",
            description="POST an image to /v1/moderate with `Authorization: Bearer <your key>`.",
            version="1.0",
        )
        # Browsers may call this API directly. Authorization uses a header (no cookies), and each
        # key can additionally be restricted to certain websites (see "origins" in keys.py).
        api.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            expose_headers=["Retry-After"],
        )

        store = keylib.KeyStore(
            modal.Dict.from_name(config.KEYS_DICT, create_if_missing=True),
            modal.Dict.from_name(config.USAGE_DICT, create_if_missing=True),
        )
        record_cache = {}  # sha256(key) -> (record or None, expires_at); revocation takes <= 30 s

        def authenticate(authorization: str, origin: str, count: bool = True):
            """Return (key_name, key_hash or None). Raises 401 / 403 / 429."""
            token = authorization.removeprefix("Bearer ").strip()
            admin_key = os.environ.get("MODERATION_API_KEY", "")
            if admin_key and token and hmac.compare_digest(token, admin_key):
                return "admin", None
            if not keylib.looks_like_key(token):
                print(json.dumps({"event": "unauthorized"}))
                raise HTTPException(status_code=401, detail="Unauthorized")

            h = keylib.hash_key(token)
            now = time.time()
            hit = record_cache.get(h)
            if hit and hit[1] > now:
                rec = hit[0]
            else:
                found = store.get_record(token)
                rec = found[1] if found else None
                if len(record_cache) > 2000:
                    record_cache.clear()
                record_cache[h] = (rec, now + 30)
            if not rec or not rec.get("active"):
                print(json.dumps({"event": "unauthorized"}))
                raise HTTPException(status_code=401, detail="Unauthorized")

            allowed_origins = rec.get("origins") or []
            if origin and allowed_origins and origin.rstrip("/").lower() not in allowed_origins:
                print(json.dumps({"event": "origin_blocked", "key": rec["name"]}))
                raise HTTPException(status_code=403, detail="This key is not allowed for this website")

            if count:
                ok, retry_after = store.use(h, rec["per_minute"], rec["per_day"])
                if not ok:
                    print(json.dumps({"event": "rate_limited", "key": rec["name"]}))
                    raise HTTPException(
                        status_code=429,
                        detail="Rate limit exceeded. Try again later.",
                        headers={"Retry-After": str(retry_after)},
                    )
            return rec["name"], h

        @api.get("/health")
        def health():
            return {"status": "ok"}

        @api.get("/v1/usage")
        def usage(authorization: str = Header(default=""), origin: str = Header(default="")):
            """Your key's limits and how much of them you have used (does not count as a request)."""
            name, h = authenticate(authorization, origin, count=False)
            if h is None:
                return {"name": name, "note": "admin key: no limits"}
            rec = record_cache[h][0]
            return {
                "name": name,
                "limits": {"per_minute": rec["per_minute"], "per_day": rec["per_day"]},
                "used": store.usage_of(h),
            }

        # Plain `def` (not async): FastAPI runs it in a worker thread, so the blocking model
        # call never freezes the event loop (e.g. /health keeps answering during inference).
        def moderate(
            file: UploadFile = File(...),
            authorization: str = Header(default=""),
            origin: str = Header(default=""),
        ):
            name, _ = authenticate(authorization, origin)

            data = file.file.read(config.MAX_IMAGE_BYTES + 1)
            try:
                result = self.clf.predict(data)
            except ImageError as e:
                print(json.dumps({"event": "bad_image", "key": name, "reason": str(e), "bytes": len(data)}))
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:  # never leak internals; the caller must keep the image private
                print(json.dumps({"event": "error", "key": name, "type": type(e).__name__}))
                raise HTTPException(status_code=500, detail="Internal error")

            # Log scores only (no image content, no filename) for monitoring and calibration.
            print(json.dumps({
                "event": "moderated", "key": name, "decision": result["decision"],
                "nsfw": result["nsfw_score"], "suggestive": result["suggestive_score"],
                "ms": result["latency_ms"], "bytes": len(data),
            }))
            return result

        api.post("/v1/moderate", response_model=ModerationResponse)(moderate)
        api.post("/moderate", response_model=ModerationResponse, include_in_schema=False)(moderate)  # old path
        return api


@app.local_entrypoint()
def main(path: str = ""):
    """modal run modal_app.py -> downloads model; --path img.jpg -> also tests it."""
    download_model.remote()
    if path:
        with open(path, "rb") as f:
            print(Moderator().predict_bytes.remote(f.read()))


# ---------------- Key maintenance (daily) ----------------
# modal.Dict entries expire after 7 days without a read or write. This job reads every key daily
# (which keeps them alive) and keeps a JSON backup in the Volume; it restores from the backup
# if the key Dict is ever found empty.
light_image = modal.Image.debian_slim(python_version="3.11").add_local_python_source("config", "keys")


@app.function(image=light_image, volumes={config.CACHE_DIR: volume}, schedule=modal.Period(days=1), timeout=300)
def maintain_keys():
    keys_dict = modal.Dict.from_name(config.KEYS_DICT, create_if_missing=True)
    usage_dict = modal.Dict.from_name(config.USAGE_DICT, create_if_missing=True)
    backup_path = os.path.join(config.CACHE_DIR, "keys_backup.json")
    volume.reload()
    records = dict(keys_dict.items())
    if not records and os.path.exists(backup_path):
        with open(backup_path) as f:
            for h, rec in json.load(f).items():
                keys_dict[h] = rec
        print(json.dumps({"event": "keys_restored_from_backup"}))
        records = dict(keys_dict.items())
    for h in records:
        usage_dict.get(h)  # a read refreshes the entry's 7-day expiry
    with open(backup_path, "w") as f:
        json.dump(records, f)
    volume.commit()
    print(json.dumps({"event": "keys_maintained", "keys": len(records)}))


# ---------------- Streamlit test / onboarding UI ----------------
ui_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("streamlit==1.40.2", "pillow", "pandas", "httpx==0.28.1")
    .add_local_python_source("config", "keys")
    .add_local_file("ui_app.py", "/root/ui_app.py")
)
ui_secret = modal.Secret.from_name("ui-auth")
ui_admin_secret = modal.Secret.from_name("ui-admin")


@app.function(image=ui_image, secrets=[ui_secret, ui_admin_secret], scaledown_window=120, timeout=3600,
              max_containers=2)
@modal.concurrent(max_inputs=10)
@modal.web_server(8501, startup_timeout=60)
def ui():
    cmd = (
        "streamlit run /root/ui_app.py --server.port 8501 --server.address 0.0.0.0 "
        "--server.headless true --server.enableCORS false --server.enableXsrfProtection false"
    )
    subprocess.Popen(cmd, shell=True)
