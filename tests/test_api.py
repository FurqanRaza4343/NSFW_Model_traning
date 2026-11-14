"""Run: pytest tests/test_api.py  (needs MODERATION_URL + MODERATION_API_KEY in .env)"""
import io
import sys
from pathlib import Path
import os

import httpx
import pytest
from dotenv import load_dotenv
from PIL import Image

load_dotenv()
URL = os.getenv("MODERATION_URL", "").rstrip("/")
KEY = os.getenv("MODERATION_API_KEY", "")
pytestmark = pytest.mark.skipif(not URL, reason="MODERATION_URL not set")


def png_bytes(color=(120, 180, 90), size=(300, 300)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def post(data, key=KEY, name="a.png"):
    return httpx.post(
        f"{URL}/moderate",
        headers={"Authorization": f"Bearer {key}"},
        files={"file": (name, data)},
        timeout=120,
    )


def test_health():
    assert httpx.get(f"{URL}/health", timeout=120).json() == {"status": "ok"}


def test_no_key_rejected():
    assert post(png_bytes(), key="wrong").status_code == 401


def test_corrupt_file():
    assert post(b"not an image").status_code == 400


def test_empty_file():
    assert post(b"").status_code == 400


def test_too_large():
    assert post(b"0" * (5 * 1024 * 1024 + 10)).status_code == 400


def test_safe_image_approved():
    r = post(png_bytes())
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "approve"
    assert set(body["scores"]) == {"normal", "nsfw"}


# ---------------- customer API keys (creates and removes a temporary key) ----------------
import time  # noqa: E402


@pytest.fixture()
def customer_key():
    modal = pytest.importorskip("modal")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import config
    import keys as keylib

    store = keylib.KeyStore(
        modal.Dict.from_name(config.KEYS_DICT, create_if_missing=True),
        modal.Dict.from_name(config.USAGE_DICT, create_if_missing=True),
    )
    name = f"pytest-{int(time.time())}"
    key, _ = store.create(name, source="admin", per_minute=3, per_day=50,
                          origins=["https://allowed.example"])
    yield key, name, store
    store.revoke(name)


def post_v1(data, key, origin=None, name="a.png"):
    headers = {"Authorization": f"Bearer {key}"}
    if origin:
        headers["Origin"] = origin
    return httpx.post(f"{URL}/v1/moderate", headers=headers, files={"file": (name, data)}, timeout=120)


def test_customer_key_works_usage_and_limit(customer_key):
    key, name, _ = customer_key
    img = png_bytes()
    for _ in range(3):
        r = post_v1(img, key)
        assert r.status_code == 200 and r.json()["decision"] == "approve"
    r = post_v1(img, key)                                   # 4th request within a minute
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    u = httpx.get(f"{URL}/v1/usage", headers={"Authorization": f"Bearer {key}"}, timeout=60).json()
    assert u["name"] == name and u["limits"] == {"per_minute": 3, "per_day": 50} and u["used"]["minute"] == 3


def test_customer_key_origin_lock(customer_key):
    key, _, _ = customer_key
    assert post_v1(png_bytes(), key, origin="https://evil.example").status_code == 403
    assert post_v1(png_bytes(), key, origin="https://allowed.example").status_code == 200


def test_unknown_key_and_old_path_and_cors():
    assert post_v1(png_bytes(), "mod_live_" + "a" * 32).status_code == 401
    assert post(png_bytes()).status_code == 200            # old /moderate + admin key still work
    r = httpx.options(f"{URL}/v1/moderate", headers={
        "Origin": "https://any.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization"}, timeout=60)
    assert r.status_code == 200 and "access-control-allow-origin" in r.headers


def test_revoked_key_rejected():
    modal = pytest.importorskip("modal")
    import config
    import keys as keylib

    store = keylib.KeyStore(modal.Dict.from_name(config.KEYS_DICT, create_if_missing=True),
                            modal.Dict.from_name(config.USAGE_DICT, create_if_missing=True))
    name = f"pytest-revoke-{int(time.time())}"
    key, _ = store.create(name, source="admin")
    assert post_v1(png_bytes(), key).status_code == 200
    store.revoke(name)
    time.sleep(35)                                          # API caches key records for 30 s
    assert post_v1(png_bytes(), key).status_code == 401
