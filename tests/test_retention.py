"""Unit tests for retention.py: the storage rules must hold on every code path."""
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import retention  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 50
JPG = b"\xff\xd8\xff\xe0" + b"y" * 50


def result(decision):
    return {"decision": decision, "nsfw_score": 0.1, "suggestive_score": 0.0}


def test_rejected_images_are_never_included_even_with_consent():
    payload, stored = retention.build_payload(result("reject"), PNG, "web", "p1", {"name": "A", "email": "a@b.co"}, True)
    assert not stored and "image_b64" not in payload
    assert payload["sha256"] == retention.image_hash(PNG) and payload["decision"] == "reject"   # metadata only


@pytest.mark.parametrize("decision", ["approve", "review"])
def test_consented_approve_and_review_images_are_included(decision):
    payload, stored = retention.build_payload(result(decision), JPG, "api", "p1", None, True)
    assert stored and base64.b64decode(payload["image_b64"]) == JPG
    assert payload["ext"] == ".jpg" and payload["mime"] == "image/jpeg"


def test_no_consent_means_no_image():
    payload, stored = retention.build_payload(result("approve"), PNG, "web", "p1", None, False)
    assert not stored and "image_b64" not in payload


def test_master_switch_off_stores_nothing(monkeypatch):
    monkeypatch.setattr(config, "STORE_IMAGES", False)
    assert not retention.should_store("approve", True)


def test_stored_decisions_can_be_narrowed(monkeypatch):
    monkeypatch.setattr(config, "STORE_DECISIONS", ("approve",))
    assert retention.should_store("approve", True) and not retention.should_store("review", True)


def test_reject_is_not_storable_by_default_config():
    assert "reject" not in config.STORE_DECISIONS


def test_magic_bytes():
    assert retention.guess_ext(PNG)[0] == ".png"
    assert retention.guess_ext(b"GIF89a....")[0] == ".gif"
    assert retention.guess_ext(b"RIFF\x00\x00\x00\x00WEBPVP8 ")[0] == ".webp"
    assert retention.guess_ext(b"nonsense")[0] == ".bin"


def test_webhook_calls_are_safe_when_not_configured():
    assert retention.record_check("", "tok", result("approve"), PNG, "web") is False
    assert retention.delete_person("not-set", "tok", "p1") is False


def test_nothing_is_sent_until_the_v2_script_is_enabled(monkeypatch):
    sent = []
    monkeypatch.setattr(retention.leadlib, "send_webhook", lambda *a, **k: sent.append(a) or True)
    monkeypatch.delenv("LEADS_SINK_VERSION", raising=False)
    assert retention.record_check("https://x", "tok", result("approve"), PNG, "web") is False
    assert retention.delete_person("https://x", "tok", "p1") is False
    assert sent == []
    monkeypatch.setenv("LEADS_SINK_VERSION", "2")
    assert retention.record_check("https://x", "tok", result("approve"), PNG, "web", consent=True) is True
    assert retention.delete_person("https://x", "tok", "p1") is True
    assert len(sent) == 2
