"""Admin CLI for customer API keys (talks to Modal directly, needs `modal setup` done).

    python manage_keys.py create --name my-shop [--per-minute 20] [--per-day 200] [--origins https://shop.com]
    python manage_keys.py list
    python manage_keys.py revoke --name my-shop

The plain key is printed ONCE at creation; only its hash is stored. Revocation takes effect
within ~30 seconds (the API caches key records for 30 s).
"""
import argparse

import modal

import config
import keys as keylib


def get_store() -> "keylib.KeyStore":
    return keylib.KeyStore(
        modal.Dict.from_name(config.KEYS_DICT, create_if_missing=True),
        modal.Dict.from_name(config.USAGE_DICT, create_if_missing=True),
    )


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="create a key")
    c.add_argument("--name", required=True)
    c.add_argument("--per-minute", type=int, default=config.DEFAULT_PER_MINUTE)
    c.add_argument("--per-day", type=int, default=config.DEFAULT_PER_DAY)
    c.add_argument("--origins", default="", help="comma separated allowed websites (optional)")

    sub.add_parser("list", help="list keys with usage")

    r = sub.add_parser("revoke", help="revoke a key by name")
    r.add_argument("--name", required=True)

    args = p.parse_args()
    store = get_store()

    if args.cmd == "create":
        try:
            key, rec = store.create(args.name, source="admin", per_minute=args.per_minute,
                                    per_day=args.per_day, origins=args.origins.split(","))
        except keylib.KeyError_ as e:
            raise SystemExit(f"Error: {e}")
        print(f"Key for '{rec['name']}' ({rec['per_minute']}/min, {rec['per_day']}/day):\n\n    {key}\n")
        print("Copy it now. It cannot be shown again.")
    elif args.cmd == "list":
        rows = store.list()
        if not rows:
            print("No keys yet.")
        for r_ in rows:
            state = "active " if r_["active"] else "REVOKED"
            print(f"{state} {r_['name']:<24} {r_['source']:<13} {r_['per_minute']}/min {r_['per_day']}/day "
                  f"used_today={r_['used_today']} total={r_['used_total']} origins={r_['origins']} id={r_['key_id']}")
    elif args.cmd == "revoke":
        n = store.revoke(args.name)
        print(f"Revoked {n} key(s) named '{args.name}'." if n else f"No active key named '{args.name}'.")


if __name__ == "__main__":
    main()
