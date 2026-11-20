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


# ---------------- documentation ----------------
def test_docs_page_served_at_root():
    r = httpx.get(f"{URL}/", timeout=120)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    html = r.text
    assert "Image Moderation API" in html
    for placeholder in ("__VERSION__", "__UI_URL__", "__PER_MIN__", "__PER_DAY__", "__MAX_MB__",
                        "__MAX_MP__", "__REJECT__", "__REVIEW__", "__SUGG__"):
        assert placeholder not in html                     # all server-side placeholders were filled
    assert "__TRIAL__" not in html
    assert "2 free" in html and "5 MB" in html            # numbers come from config
    assert 'id="post-moderate"' in html and "/v1/usage" in html


def test_swagger_and_openapi():
    assert httpx.get(f"{URL}/docs", timeout=60).status_code == 200
    spec = httpx.get(f"{URL}/openapi.json", timeout=60).json()
    assert "/v1/moderate" in spec["paths"] and "/v1/usage" in spec["paths"]
    assert "/moderate" not in spec["paths"]               # legacy path is hidden from the docs
    assert "HTTPBearer" in spec["components"]["securitySchemes"]
    assert set(spec["paths"]["/v1/moderate"]["post"]["responses"]) >= {"200", "400", "401", "403", "429", "500"}


# ---------------- free trial (registered person) and unlimited keys ----------------
@pytest.fixture()
def lead_setup():
    modal = pytest.importorskip("modal")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import config
    import keys as keylib
    import leads as leadlib

    usage = modal.Dict.from_name(config.USAGE_DICT, create_if_missing=True)
    ks = keylib.KeyStore(modal.Dict.from_name(config.KEYS_DICT, create_if_missing=True), usage)
    ls = leadlib.LeadStore(modal.Dict.from_name(config.LEADS_DICT, create_if_missing=True), usage)
    email = f"pytest{int(time.time())}@example.com"
    lid, _, _ = ls.register("Pytest User", email, "", True)
    yield ks, ls, lid
    for prefix in ("lead-", "vip-"):
        ks.revoke(prefix + lid[:10])
    d = modal.Dict.from_name(config.KEYS_DICT)
    for h, r in list(d.items()):
        if r["name"].endswith(lid[:10]):
            d.pop(h)
    ls.delete(email)


def test_free_trial_key_allows_two_checks_then_429(lead_setup):
    ks, ls, lid = lead_setup
    key, _ = ks.create("lead-" + lid[:10], source="self_service", lead=lid)
    img = png_bytes()
    assert post_v1(img, key).status_code == 200
    assert post_v1(img, key).status_code == 200
    r = post_v1(img, key)
    assert r.status_code == 429 and "trial" in r.json()["detail"].lower()
    u = httpx.get(f"{URL}/v1/usage", headers={"Authorization": f"Bearer {key}"}, timeout=60).json()
    assert u["trial"] == {"limit": 2, "used": 2}


def test_unlimited_key_is_never_blocked(lead_setup):
    ks, ls, lid = lead_setup
    key, _ = ks.create("vip-" + lid[:10], source="self_service", lead=lid, unlimited=True)
    for _ in range(5):                                      # more than the trial and far above any rate limit
        assert post_v1(png_bytes(), key).status_code == 200
    u = httpx.get(f"{URL}/v1/usage", headers={"Authorization": f"Bearer {key}"}, timeout=60).json()
    assert u["unlimited"] is True and u["used"]["total"] >= 5
