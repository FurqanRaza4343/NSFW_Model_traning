"""Customer API keys: creation, lookup, revocation and rate limiting.

Storage is any dict-like object (modal.Dict in production, a plain dict in tests).
Only sha256(key) is stored; the plain key is shown once, at creation.
"""
import hashlib
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import config

KEY_PREFIX = "mod_live_"
NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]{2,40}$")


class KeyError_(Exception):
    """Base error for key management (message is safe to show to users)."""


class CapReached(KeyError_):
    pass


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(24)


def looks_like_key(token: str) -> bool:
    return token.startswith(KEY_PREFIX) and 20 <= len(token) <= 100


def normalize_origins(origins) -> List[str]:
    out = []
    for o in origins or []:
        o = o.strip().rstrip("/").lower()
        if o:
            out.append(o)
    return out


def check_and_count(usage: Optional[dict], now: float, per_minute: int, per_day: int
                    ) -> Tuple[bool, dict, int]:
    """Return (allowed, new_usage, retry_after_seconds). Rejected requests are not counted."""
    u = dict(usage or {})
    minute, day = int(now // 60), int(now // 86400)
    if u.get("m") != minute:
        u["m"], u["mc"] = minute, 0
    if u.get("d") != day:
        u["d"], u["dc"] = day, 0
    if u["mc"] >= per_minute:
        return False, u, max(1, 60 - int(now % 60))
    if u["dc"] >= per_day:
        return False, u, max(1, 86400 - int(now % 86400))
    u["mc"] += 1
    u["dc"] += 1
    u["total"] = u.get("total", 0) + 1
    u["last"] = int(now)
    return True, u, 0


class KeyStore:
    def __init__(self, keys, usage, clock=time.time):
        self.keys = keys
        self.usage = usage
        self.clock = clock

    # ----- management -----
    def _records(self) -> List[Tuple[str, dict]]:
        return list(self.keys.items())

    def count_self_service(self) -> int:
        return sum(1 for _, r in self._records() if r.get("source") == "self_service" and r.get("active"))

    def create(self, name: str, source: str = "admin", per_minute: Optional[int] = None,
               per_day: Optional[int] = None, origins=None) -> Tuple[str, dict]:
        name = (name or "").strip()
        if not NAME_RE.match(name):
            raise KeyError_("Name must be 2-40 characters: letters, digits, space, dot, dash, underscore.")
        records = self._records()
        if any(r.get("name", "").lower() == name.lower() and r.get("active") for _, r in records):
            raise KeyError_(f"A key named '{name}' already exists. Choose another name.")
        if source == "self_service" and self.count_self_service() >= config.MAX_SELF_SERVICE_KEYS:
            raise CapReached("The limit of self-service keys has been reached. Ask the admin for a key.")
        key = generate_key()
        record = {
            "name": name,
            "created": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds"),
            "per_minute": int(per_minute or config.DEFAULT_PER_MINUTE),
            "per_day": int(per_day or config.DEFAULT_PER_DAY),
            "origins": normalize_origins(origins),
            "active": True,
            "source": source,
        }
        self.keys[hash_key(key)] = record
        return key, record

    def revoke(self, name: str) -> int:
        """Deactivate every active key with this name. Returns how many were revoked."""
        n = 0
        for h, r in self._records():
            if r.get("name", "").lower() == name.strip().lower() and r.get("active"):
                r = dict(r, active=False, revoked=datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds"))
                self.keys[h] = r
                n += 1
        return n

    def list(self) -> List[Dict]:
        rows = []
        for h, r in self._records():
            u = self.usage.get(h) or {}
            today = int(self.clock() // 86400)
            rows.append({
                "name": r.get("name"), "active": r.get("active"), "source": r.get("source"),
                "created": r.get("created"), "per_minute": r.get("per_minute"), "per_day": r.get("per_day"),
                "origins": ", ".join(r.get("origins") or []) or "any",
                "used_today": u.get("dc", 0) if u.get("d") == today else 0,
                "used_total": u.get("total", 0),
                "key_id": h[:8],
            })
        return sorted(rows, key=lambda x: x["created"] or "")

    # ----- request-time -----
    def get_record(self, key: str) -> Optional[Tuple[str, dict]]:
        h = hash_key(key)
        r = self.keys.get(h)
        return (h, r) if r else None

    def use(self, h: str, per_minute: int, per_day: int) -> Tuple[bool, int]:
        allowed, new_usage, retry = check_and_count(self.usage.get(h), self.clock(), per_minute, per_day)
        if allowed:
            self.usage[h] = new_usage
        return allowed, retry

    def usage_of(self, h: str) -> dict:
        u = self.usage.get(h) or {}
        now = self.clock()
        return {
            "minute": u.get("mc", 0) if u.get("m") == int(now // 60) else 0,
            "day": u.get("dc", 0) if u.get("d") == int(now // 86400) else 0,
            "total": u.get("total", 0),
        }
