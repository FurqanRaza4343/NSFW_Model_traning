"""Unit tests for leads.py (no network: plain dicts instead of modal.Dict)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import leads  # noqa: E402


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def store():
    c = Clock()
    return leads.LeadStore({}, {}, clock=c), c


def register(s, email="Ali@Example.com", name="Ali Khan", phone="+92 300 1234567"):
    return s.register(name, email, phone, True)


def test_register_normalises_and_is_idempotent():
    s, _ = store()
    lid, rec, new = register(s)
    assert new and rec["email"] == "ali@example.com" and rec["trial_used"] == 0
    lid2, rec2, new2 = s.register("Someone Else", "ALI@example.com", "", True)
    assert lid2 == lid and not new2
    assert rec2["name"] == "Ali Khan"                 # details of an existing lead are never overwritten


@pytest.mark.parametrize("name,email,phone,consent", [
    ("A", "a@b.co", "", True),                         # name too short
    ("Ali Khan", "not-an-email", "", True),
    ("Ali Khan", "a@b.co", "12", True),                # phone too short
    ("Ali Khan", "a@b.co", "abc-defg-hij", True),
    ("Ali Khan", "a@b.co", "", False),                 # no consent
])
def test_validation(name, email, phone, consent):
    s, _ = store()
    with pytest.raises(leads.LeadError):
        s.register(name, email, phone, consent)


def test_phone_is_optional_unless_required(monkeypatch):
    s, _ = store()
    assert s.register("Ali Khan", "a@b.co", "", True)[2]
    monkeypatch.setattr(config, "REQUIRE_PHONE", True)
    with pytest.raises(leads.LeadError):
        s.register("Bo Lee", "b@b.co", "", True)


def test_trial_is_two_checks_total():
    s, _ = store()
    lid, _, _ = register(s)
    assert s.trial_left(lid) == config.TRIAL_CHECKS == 2
    assert s.consume_trial(lid) == (True, "")
    assert s.consume_trial(lid) == (True, "")
    assert s.consume_trial(lid) == (False, "trial_used")
    assert s.trial_left(lid) == 0
    s.register("Ali Khan", "ali@example.com", "", True)  # registering again does not reset it
    assert s.trial_left(lid) == 0


def test_refund_returns_a_check():
    s, _ = store()
    lid, _, _ = register(s)
    s.consume_trial(lid)
    s.refund_trial(lid)
    assert s.trial_left(lid) == 2


def test_global_daily_cap(monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_TRIAL_PER_DAY", 3)
    s, clock = store()
    a, _, _ = register(s, "a@x.com")
    b, _, _ = register(s, "b@x.com")
    assert s.consume_trial(a)[0] and s.consume_trial(a)[0] and s.consume_trial(b)[0]
    assert s.consume_trial(b) == (False, "capacity")
    clock.t += 86400                                      # next day: capacity is back
    assert s.consume_trial(b)[0]


def test_unknown_lead_cannot_consume():
    s, _ = store()
    assert s.consume_trial("nope") == (False, "unknown")


def test_delete_and_list():
    s, _ = store()
    register(s)
    assert len(s.list()) == 1
    assert s.delete("ALI@example.com") and not s.delete("ali@example.com")
    assert s.list() == []


def test_code_verification_and_lockout():
    usage, clock = {}, Clock()
    ok, _ = leads.verify_code(usage, "secret-code", "secret-code", "lead1", clock)
    assert ok
    assert leads.verify_code(usage, "x", "", "lead1", clock)[0] is False          # disabled when no code is set
    for _ in range(5):
        assert leads.verify_code(usage, "wrong", "secret-code", "lead1", clock)[0] is False
    ok, msg = leads.verify_code(usage, "secret-code", "secret-code", "lead1", clock)
    assert not ok and "Too many" in msg                                           # locked for this email today
    assert leads.verify_code(usage, "secret-code", "secret-code", "lead2", clock)[0]   # others unaffected
    clock.t += 86400
    assert leads.verify_code(usage, "secret-code", "secret-code", "lead1", clock)[0]   # lock resets daily


def test_global_code_lockout():
    usage, clock = {}, Clock()
    for i in range(60):
        leads.verify_code(usage, "bad", "secret", f"l{i}", clock)
    ok, msg = leads.verify_code(usage, "secret", "secret", "fresh", clock)
    assert not ok and "later" in msg


def test_webhook_is_safe_when_not_configured():
    assert leads.send_webhook("", "tok", {"a": 1}) is False
    assert leads.send_webhook("not-set", "tok", {"a": 1}) is False
