"""Per-check records and consented image retention, sent to the owner's Google Apps Script.

Rules enforced here (not in the UI), so every code path follows them:
  * An image is only sent for storage when the person agreed to it (registration consent, or an owner-enabled key).
  * Only decisions in config.STORE_DECISIONS are stored (approve / review). REJECTED images are NEVER sent:
    for them only the decision, scores, size and a SHA-256 fingerprint are recorded.
  * The Apps Script deletes stored images after config.IMAGE_RETENTION_DAYS and on request (delete_person).
"""
import base64
import hashlib
import os
import threading
from typing import Optional, Tuple

import config
import leads as leadlib


def sink_v2() -> bool:
    """The Google Apps Script must be version 2 (it understands "check" and "delete_person").
    Until the owner has deployed it, nothing is sent: the old script would add junk rows to the Leads sheet."""
    return os.environ.get("LEADS_SINK_VERSION") == "2"


def image_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def guess_ext(data: bytes) -> Tuple[str, str]:
    """(extension, mime) from the file's magic bytes."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg", "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png", "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif", "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if data[:2] == b"BM":
        return ".bmp", "image/bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tif", "image/tiff"
    return ".bin", "application/octet-stream"


def should_store(decision: str, consent: bool) -> bool:
    return bool(config.STORE_IMAGES and consent and decision in config.STORE_DECISIONS)


def build_payload(result: dict, data: bytes, source: str, person: str = "", lead: Optional[dict] = None,
                  consent: bool = False, label: str = "") -> Tuple[dict, bool]:
    """Return (payload, image_included)."""
    store = should_store(result["decision"], consent)
    payload = {
        "action": "check", "source": source, "decision": result["decision"],
        "nsfw": result["nsfw_score"], "suggestive": result["suggestive_score"],
        "bytes": len(data), "sha256": image_hash(data),
        "name": (lead or {}).get("name", ""), "email": (lead or {}).get("email", ""),
        "person": person, "label": label,
    }
    if store:
        ext, mime = guess_ext(data)
        payload.update(image_b64=base64.b64encode(data).decode(), ext=ext, mime=mime)
    return payload, store


def record_check(url: str, token: str, result: dict, data: bytes, source: str, person: str = "",
                 lead: Optional[dict] = None, consent: bool = False, label: str = "") -> bool:
    if not sink_v2():
        return False
    payload, _ = build_payload(result, data, source, person, lead, consent, label)
    return leadlib.send_webhook(url, token, payload, timeout=60.0)


def record_check_async(*args, **kwargs) -> None:
    """Fire and forget: the visitor never waits for Google. Failures are ignored (best effort)."""
    threading.Thread(target=lambda: record_check(*args, **kwargs), daemon=True).start()


def delete_person(url: str, token: str, person: str) -> bool:
    """Ask the Apps Script to trash every stored image of this person."""
    if not sink_v2():
        return False
    return leadlib.send_webhook(url, token, {"action": "delete_person", "person": person})
