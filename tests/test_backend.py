"""Unit tests for backend_client (no network: uses httpx.MockTransport)."""
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend_client import ModerationClient  # noqa: E402


def make_client(handler, retries=1):
    c = ModerationClient("https://x.test", "key", timeout_s=2, retries=retries,
                         transport=httpx.MockTransport(handler))
    return c


def run(coro):
    return asyncio.run(coro)


def test_approve_publishes():
    c = make_client(lambda r: httpx.Response(200, json={"decision": "approve", "nsfw_score": 0.01}))
    assert run(c.check(b"x")).status == "publish"


def test_review_is_pending():
    c = make_client(lambda r: httpx.Response(200, json={"decision": "review", "nsfw_score": 0.4}))
    assert run(c.check(b"x")).status == "pending_review"


def test_reject_is_rejected():
    c = make_client(lambda r: httpx.Response(200, json={"decision": "reject", "nsfw_score": 0.9}))
    assert run(c.check(b"x")).status == "rejected"


def test_unexpected_payload_is_private():
    c = make_client(lambda r: httpx.Response(200, json={"decision": "maybe"}))
    assert run(c.check(b"x")).status == "private"


def test_bad_key_is_private_without_retry():
    calls = []

    def handler(r):
        calls.append(1)
        return httpx.Response(401, json={"detail": "Unauthorized"})

    res = run(make_client(handler, retries=3).check(b"x"))
    assert res.status == "private" and len(calls) == 1


def test_server_error_retries_then_private():
    calls = []

    def handler(r):
        calls.append(1)
        return httpx.Response(503)

    res = run(make_client(handler, retries=1).check(b"x"))
    assert res.status == "private" and len(calls) == 2


def test_timeout_is_private():
    def handler(r):
        raise httpx.ReadTimeout("slow")

    assert run(make_client(handler, retries=0).check(b"x")).status == "private"


def test_recovers_after_one_failure():
    state = {"n": 0}

    def handler(r):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(502)
        return httpx.Response(200, json={"decision": "approve", "nsfw_score": 0.0})

    assert run(make_client(handler, retries=2).check(b"x")).status == "publish"
