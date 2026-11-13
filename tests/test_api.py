"""Run: pytest tests/test_api.py  (needs MODERATION_URL + MODERATION_API_KEY in .env)"""
import io
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
