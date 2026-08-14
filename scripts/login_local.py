#!/usr/bin/env python3
"""One-time setup: log in to Garmin from your own machine and mint the secrets.

Run this on your Mac, not in CI:

    python3 scripts/login_local.py

It logs in (handling 2FA if your account has it), then prints the two values to
paste into GitHub → Settings → Secrets and variables → Actions:

    GARMIN_TOKENS   the session token, used once to bootstrap
    TOKEN_KEY       a fresh random key the workflow uses to seal the rolling token

It also prints the activity types found in your account, so you can confirm which
typeKey your BJJ sessions actually use before editing config.json.
"""

from __future__ import annotations

import argparse
import collections
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from garmin_client import connect, dump_token  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "config.json"
RULE = "─" * 68


def activity_types(client) -> list[tuple[str, int, str]]:
    """(typeKey, count, example name) across the most recent 100 activities."""
    recent = client.get_activities(0, 100) or []
    counts: collections.Counter[str] = collections.Counter()
    names: dict[str, str] = {}
    for a in recent:
        key = (a.get("activityType") or {}).get("typeKey", "unknown")
        counts[key] += 1
        names.setdefault(key, a.get("activityName", ""))
    return [(k, n, names[k]) for k, n in counts.most_common()]


def confirm_bjj_type(types: list[tuple[str, int, str]], interactive: bool) -> None:
    """Check config against what's really in the account, and offer to fix it."""
    cfg = json.loads(CONFIG.read_text())
    configured = cfg["bjj_type_keys"]
    seen = {t[0] for t in types}

    print(f"\n{RULE}\nActivity types in your account\n{RULE}")
    if not types:
        print("  (no activities found — nothing to check)")
        return
    for i, (key, n, name) in enumerate(types, 1):
        mark = "←  currently set as BJJ" if key in configured else ""
        print(f"  {i:>2}. {key:<28} {n:>3} of last 100   {name[:24]:<24} {mark}")

    if any(k in seen for k in configured):
        print(f"\n  Looks right: '{configured[0]}' is in your account.")
        return

    print(f"\n  ⚠  None of your recent activities use '{configured[0]}'.")
    if not interactive:
        print("     Edit \"bjj_type_keys\" in config.json before the first sync.")
        return

    answer = input(
        "\n  Which number are your BJJ sessions? (Enter to leave config alone): "
    ).strip()
    if not answer.isdigit() or not (1 <= int(answer) <= len(types)):
        print("     Left config.json unchanged.")
        return

    chosen = types[int(answer) - 1][0]
    cfg["bjj_type_keys"] = [chosen]
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    print(f"     Set bjj_type_keys to [\"{chosen}\"] in config.json.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--secrets-to",
        metavar="DIR",
        help="write the two secret values into DIR instead of printing them "
             "(used by setup.sh so nothing has to be copied by hand)",
    )
    args = ap.parse_args()

    print("Logging in to Garmin Connect...\n")
    client = connect(allow_password=True)

    token_json = dump_token(client)
    token_key = secrets.token_urlsafe(32)

    try:
        confirm_bjj_type(activity_types(client), interactive=True)
    except Exception as exc:  # noqa: BLE001 - informational only
        print(f"  Could not list activities: {exc}")

    if args.secrets_to:
        out = Path(args.secrets_to)
        out.mkdir(parents=True, exist_ok=True)
        for name, value in (("garmin_tokens", token_json), ("token_key", token_key)):
            f = out / f"{name}.txt"
            f.write_text(value)
            f.chmod(0o600)
        print(f"\n{RULE}\nSecrets written for the installer to pick up.\n{RULE}")
        return 0

    print(f"\n{RULE}\nGitHub secret 1 of 2 — name it  GARMIN_TOKENS\n{RULE}")
    print(token_json)
    print(f"\n{RULE}\nGitHub secret 2 of 2 — name it  TOKEN_KEY\n{RULE}")
    print(token_key)
    print(f"\n{RULE}")
    print(
        "Keep both out of the repo and out of chat logs. TOKEN_KEY is only ever\n"
        "needed again if you re-run this login: it is what decrypts the rolling\n"
        "token the workflow commits, so if you lose it, just run this script again."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
