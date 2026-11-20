"""Admin CLI for registered users (leads). Talks to Modal directly (needs `modal setup` done).

    python manage_leads.py list
    python manage_leads.py export leads.csv
    python manage_leads.py delete --email someone@example.com     # privacy request: removes the person and their keys

The same data is visible in the UI Admin tab and (if configured) in your Google Sheet.
"""
import argparse
import csv

import modal

import config
import keys as keylib
import leads as leadlib


def stores():
    usage = modal.Dict.from_name(config.USAGE_DICT, create_if_missing=True)
    ls = leadlib.LeadStore(modal.Dict.from_name(config.LEADS_DICT, create_if_missing=True), usage)
    ks = keylib.KeyStore(modal.Dict.from_name(config.KEYS_DICT, create_if_missing=True), usage)
    return ls, ks


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    e = sub.add_parser("export")
    e.add_argument("path")
    d = sub.add_parser("delete")
    d.add_argument("--email", required=True)
    args = p.parse_args()
    ls, ks = stores()

    if args.cmd == "list":
        rows = ls.list()
        print(f"{len(rows)} registered")
        for r in rows:
            print(f"{r.get('created', '')[:19]}  {r['name']:<24} {r['email']:<32} {r.get('phone', ''):<18} "
                  f"trial_used={r.get('trial_used', 0)} sheet={'yes' if r.get('webhook_sent') else 'no'}")
    elif args.cmd == "export":
        rows = ls.list()
        with open(args.path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["name", "email", "phone", "created", "consent_at", "trial_used",
                                              "webhook_sent", "source"], extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"Exported {len(rows)} rows to {args.path} (contains personal data: keep it private)")
    elif args.cmd == "delete":
        if ls.delete(args.email):
            lid = leadlib.lead_id(args.email)
            for prefix in ("lead-", "vip-"):
                ks.revoke(prefix + lid[:10])
            print("Deleted the person and revoked their keys. Remember to delete their row in the Google Sheet too.")
        else:
            print("No lead with that email.")


if __name__ == "__main__":
    main()
