"""Unit tests for keys.py (no network: uses plain dicts instead of modal.Dict)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import keys  # noqa: E402


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_store():
    clock = Clock()
    return keys.KeyStore({}, {}, clock=clock), clock


def test_create_stores_only_hash():
    store, _ = make_store()
    key, rec = store.create("shop-site", source="self_service")
    assert key.startswith("mod_live_") and keys.looks_like_key(key)
    assert key not in store.keys                       # plain key is never stored
    assert keys.hash_key(key) in store.keys
    assert rec["per_minute"] == config.DEFAULT_PER_MINUTE and rec["per_day"] == config.DEFAULT_PER_DAY


def test_lookup_and_revoke():
    store, _ = make_store()
    key, _ = store.create("app one")
    h, rec = store.get_record(key)
    assert rec["active"]
    assert store.revoke("APP ONE") == 1                # case-insensitive
    assert not store.get_record(key)[1]["active"]
    assert store.get_record("mod_live_" + "x" * 30) is None


def test_name_rules_and_duplicates():
    store, _ = make_store()
    with pytest.raises(keys.KeyError_):
        store.create("x")                              # too short
    with pytest.raises(keys.KeyError_):
        store.create("bad<name>")
    store.create("same")
    with pytest.raises(keys.KeyError_):
        store.create("SAME")                           # duplicate (case-insensitive)


def test_self_service_cap():
    store, _ = make_store()
    for i in range(config.MAX_SELF_SERVICE_KEYS):
        store.create(f"user {i}", source="self_service")
    with pytest.raises(keys.CapReached):
        store.create("one too many", source="self_service")
    store.create("admin made", source="admin")         # admin keys are not capped


def test_per_minute_limit_and_reset():
    store, clock = make_store()
    key, _ = store.create("lim", per_minute=3, per_day=100)
    h, _ = store.get_record(key)
    assert [store.use(h, 3, 100)[0] for _ in range(3)] == [True, True, True]
    allowed, retry = store.use(h, 3, 100)
    assert not allowed and 1 <= retry <= 60
    clock.t += 61                                      # next minute
    assert store.use(h, 3, 100)[0]


def test_per_day_limit_and_reset():
    store, clock = make_store()
    key, _ = store.create("daily", per_minute=100, per_day=2)
    h, _ = store.get_record(key)
    assert store.use(h, 100, 2)[0] and store.use(h, 100, 2)[0]
    allowed, retry = store.use(h, 100, 2)
    assert not allowed and retry > 60
    clock.t += 86400
    assert store.use(h, 100, 2)[0]


def test_rejected_requests_are_not_counted():
    store, _ = make_store()
    key, _ = store.create("cnt", per_minute=1, per_day=10)
    h, _ = store.get_record(key)
    store.use(h, 1, 10)
    for _ in range(5):
        store.use(h, 1, 10)
    assert store.usage_of(h)["total"] == 1


def test_list_shows_usage_without_secrets():
    store, _ = make_store()
    key, _ = store.create("shown", origins=["https://Example.com/"])
    h, _ = store.get_record(key)
    store.use(h, 20, 200)
    row = store.list()[0]
    assert row["used_today"] == 1 and row["origins"] == "https://example.com"
    assert key not in str(store.list())
