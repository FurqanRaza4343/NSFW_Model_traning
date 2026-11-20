"""Lead capture, free-trial accounting and access-code protection.

Storage is any dict-like object (modal.Dict in production, a plain dict in tests).
Privacy: only name, email, optional phone and consent time are stored. Images are never stored.
"""
import hashlib
import hmac
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import config

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()\-]{5,22}$")

TRIAL_MESSAGES = {
    "trial_used": "Your free trial is used up. Enter the access code from the owner for unlimited use.",
    "capacity": "Today's free capacity has been reached. Please try again tomorrow, or use an access code.",
    "unknown": "Please register first.",
}


class LeadError(Exception):
    """Validation error; the message is safe to show to the user."""


def lead_id(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def clean(name: str, email: str, phone: str, consent: bool, require_phone: bool = False) -> Dict[str, str]:
    name = " ".join((name or "").split())
    email = (email or "").strip().lower()
    phone = (phone or "").strip()
    if not (2 <= len(name) <= 80):
        raise LeadError("Please enter your full name (2 to 80 characters).")
    if len(email) > 120 or not EMAIL_RE.match(email):
        raise LeadError("Please enter a valid email address.")
    if phone:
        digits = re.sub(r"\D", "", phone)
        if not PHONE_RE.match(phone) or not (7 <= len(digits) <= 15):
            raise LeadError("Please enter a valid phone number (digits, +, spaces, dashes).")
    elif require_phone:
        raise LeadError("Please enter your phone number.")
    if not consent:
        raise LeadError("Please tick the consent box to continue.")
    return {"name": name, "email": email, "phone": phone}


class LeadStore:
    def __init__(self, leads, usage, clock=time.time):
        self.leads = leads
        self.usage = usage
        self.clock = clock

    def _now_iso(self) -> str:
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds")

    # ----- registration -----
    def register(self, name: str, email: str, phone: str, consent: bool) -> Tuple[str, dict, bool]:
        """Return (lead_id, record, is_new). An existing email just continues (trial is never reset,
        and details from an unauthenticated visitor never overwrite what is stored)."""
        data = clean(name, email, phone, consent, config.REQUIRE_PHONE)
        lid = lead_id(data["email"])
        rec = self.leads.get(lid)
        if rec:
            return lid, rec, False
        rec = {**data, "created": self._now_iso(), "consent_at": self._now_iso(),
               "trial_used": 0, "webhook_sent": False, "source": "ui",
               "image_consent": True}        # the registration text covers keeping approved/review images
        self.leads[lid] = rec
        return lid, rec, True

    def get(self, lid: str) -> Optional[dict]:
        return self.leads.get(lid)

    def delete(self, email: str) -> bool:
        lid = lead_id(email)
        if self.leads.get(lid) is None:
            return False
        self.leads.pop(lid)
        return True

    def list(self) -> List[dict]:
        rows = []
        for lid, r in self.leads.items():
            rows.append({**r, "id": lid[:8]})
        return sorted(rows, key=lambda x: x.get("created", ""))

    # ----- free trial -----
    def trial_left(self, lid: str) -> int:
        rec = self.get(lid)
        return max(0, config.TRIAL_CHECKS - (rec or {}).get("trial_used", 0)) if rec else 0

    def _day_key(self) -> str:
        return f"trial:{int(self.clock() // 86400)}"

    def consume_trial(self, lid: str) -> Tuple[bool, str]:
        rec = self.get(lid)
        if not rec:
            return False, "unknown"
        if rec.get("trial_used", 0) >= config.TRIAL_CHECKS:
            return False, "trial_used"
        if (self.usage.get(self._day_key()) or 0) >= config.GLOBAL_TRIAL_PER_DAY:
            return False, "capacity"
        rec = dict(rec, trial_used=rec.get("trial_used", 0) + 1)
        self.leads[lid] = rec
        self.usage[self._day_key()] = (self.usage.get(self._day_key()) or 0) + 1
        return True, ""

    def refund_trial(self, lid: str) -> None:
        """Give a check back when the model failed (the visitor did not get a result)."""
        rec = self.get(lid)
        if rec and rec.get("trial_used", 0) > 0:
            self.leads[lid] = dict(rec, trial_used=rec["trial_used"] - 1)
            k = self._day_key()
            self.usage[k] = max(0, (self.usage.get(k) or 0) - 1)

    def mark_webhook(self, lid: str, ok: bool = True) -> None:
        rec = self.get(lid)
        if rec:
            self.leads[lid] = dict(rec, webhook_sent=ok)


def verify_code(usage, entered: str, expected: str, lid: str, clock=time.time,
                max_per_lead_per_day: int = 5, max_global_per_hour: int = 60) -> Tuple[bool, str]:
    """Check an access code with brute-force protection (per email per day, and globally per hour)."""
    if not expected:
        return False, "Access codes are not enabled."
    now = clock()
    gk, lk = f"codefail:g:{int(now // 3600)}", f"codefail:l:{lid}:{int(now // 86400)}"
    if (usage.get(gk) or 0) >= max_global_per_hour:
        return False, "Too many attempts. Please try again later."
    if (usage.get(lk) or 0) >= max_per_lead_per_day:
        return False, "Too many wrong codes for this email today. Please contact the owner."
    if hmac.compare_digest((entered or "").encode(), expected.encode()):
        return True, ""
    usage[gk] = (usage.get(gk) or 0) + 1
    usage[lk] = (usage.get(lk) or 0) + 1
    return False, "Wrong access code."


def send_webhook(url: str, token: str, payload: dict, timeout: float = 15.0) -> bool:
    """POST a lead event to the Google Apps Script web app. Never raises."""
    if not url or not url.startswith("http") or not token:
        return False
    try:
        import httpx

        r = httpx.post(url, json={"token": token, **payload}, timeout=timeout, follow_redirects=True)
        return r.status_code == 200 and r.text.strip().lower().startswith("ok")
    except Exception:
        return False


def lead_payload(rec: dict, event: str = "registered") -> dict:
    """What is sent to the Google Sheet (Apps Script). No images, no keys, no access code."""
    return {
        "event": event, "name": rec.get("name", ""), "email": rec.get("email", ""), "phone": rec.get("phone", ""),
        "consent_at": rec.get("consent_at", ""), "created": rec.get("created", ""), "source": rec.get("source", "ui"),
    }
