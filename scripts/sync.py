#!/usr/bin/env python3
"""Pull training data from Garmin Connect into data/.

First run backfills the whole account history. Every run after that fetches a
short overlapping window and merges. Expensive per-activity extras (heart-rate
zones) and per-day extras (VO2 max) are fetched under a small per-run budget, so
a long history catches up over a few days instead of hammering the API once.

    python3 scripts/sync.py              # normal incremental run
    python3 scripts/sync.py --full       # force a complete re-scan of history
    python3 scripts/sync.py --days 60    # custom look-back window
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from garmin_client import connect, persist_token  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
ACTIVITIES_FILE = DATA_DIR / "activities.json"
DAILY_FILE = DATA_DIR / "daily_metrics.json"
STATE_FILE = DATA_DIR / "sync_state.json"

PAGE_SIZE = 100
OVERLAP_DAYS = 21

# Fields worth keeping from the activity list response. Everything else is noise
# for this dashboard and would bloat the committed JSON.
KEEP_FIELDS = (
    "activityId",
    "activityName",
    "startTimeLocal",
    "startTimeGMT",
    "duration",
    "elapsedDuration",
    "movingDuration",
    "distance",
    "calories",
    "averageHR",
    "maxHR",
    "aerobicTrainingEffect",
    "anaerobicTrainingEffect",
    "activityTrainingLoad",
    "moderateIntensityMinutes",
    "vigorousIntensityMinutes",
    "hrTimeInZone_1",
    "hrTimeInZone_2",
    "hrTimeInZone_3",
    "hrTimeInZone_4",
    "hrTimeInZone_5",
)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        print(f"  ! {path.name} was unreadable; starting fresh")
        return default


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True))


def trim(activity: dict) -> dict:
    out = {k: activity.get(k) for k in KEEP_FIELDS if activity.get(k) is not None}
    atype = activity.get("activityType") or {}
    out["typeKey"] = atype.get("typeKey", "unknown")
    out["parentTypeId"] = atype.get("parentTypeId")
    out["activityId"] = activity.get("activityId")
    return out


def fetch_all_activities(client, delay: float) -> list[dict]:
    """Page through the full account history."""
    collected: list[dict] = []
    start = 0
    while True:
        batch = client.get_activities(start, PAGE_SIZE)
        if isinstance(batch, dict):
            batch = batch.get("activityList", [])
        if not batch:
            break
        collected.extend(batch)
        print(f"  fetched {len(collected)} activities...", flush=True)
        start += PAGE_SIZE
        time.sleep(delay)
        if start > 20000:  # safety valve
            print("  ! stopping at 20,000 activities")
            break
    return collected


def fetch_recent_activities(client, days: int) -> list[dict]:
    start = (date.today() - timedelta(days=days)).isoformat()
    end = date.today().isoformat()
    print(f"  window {start} → {end}")
    batch = client.get_activities_by_date(start, end)
    return batch or []


def enrich_hr_zones(client, activities: dict, bjj_ids: list, budget: int, delay: float) -> int:
    """Fill in per-zone seconds for BJJ sessions that lack them."""
    pending = [
        aid
        for aid in bjj_ids
        if not activities[aid].get("hrTimeInZone_1")
        and not activities[aid].get("zonesUnavailable")
    ]
    if not pending:
        return 0
    # Newest first: recent sessions matter most on the dashboard.
    pending.sort(key=lambda a: activities[a].get("startTimeLocal", ""), reverse=True)
    done = failed = 0
    for aid in pending[:budget]:
        try:
            zones = client.get_activity_hr_in_timezones(str(aid))
            if zones:
                for z in zones:
                    number = z.get("zoneNumber")
                    secs = z.get("secsInZone")
                    if number and secs is not None:
                        activities[aid][f"hrTimeInZone_{number}"] = round(float(secs), 1)
                done += 1
            else:
                # A clean empty answer means this session genuinely has no zone
                # data (no HR recorded) — don't ask again.
                activities[aid]["zonesUnavailable"] = True
        except Exception as exc:  # noqa: BLE001 - one bad activity must not kill the run
            # An error might just be rate limiting, so leave it queued for tomorrow.
            print(f"  ! zones for {aid}: {exc}")
            failed += 1
            if failed >= 5:
                print("  ! giving up on zone enrichment for this run")
                break
        time.sleep(delay)
    remaining = max(0, len(pending) - budget)
    print(f"  heart-rate zones: +{done} enriched, {remaining} still queued")
    return done


def backfill_vo2max(client, daily: dict, first_day: date, budget: int, delay: float) -> int:
    """Sample VO2 max weekly, newest first, within the per-run budget."""
    wanted: list[str] = []
    cursor = date.today()
    while cursor >= first_day and len(wanted) < 400:
        key = cursor.isoformat()
        if key not in daily:
            wanted.append(key)
        cursor -= timedelta(days=7)

    done = failed = 0
    for key in wanted[:budget]:
        entry: dict = {}
        try:
            metrics = client.get_max_metrics(key)
            if isinstance(metrics, list) and metrics:
                generic = metrics[0].get("generic") or {}
                value = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
                if value:
                    entry["vo2max"] = round(float(value), 1)
            # A clean empty answer is cached too, so we ask about that date once.
            daily[key] = entry
            done += 1
        except Exception as exc:  # noqa: BLE001 - transient errors must stay retryable
            print(f"  ! vo2max for {key}: {exc}")
            failed += 1
            if failed >= 5:
                print("  ! giving up on VO2 max for this run")
                break
        time.sleep(delay)
    remaining = max(0, len(wanted) - budget)
    print(f"  VO2 max samples: +{done} fetched, {remaining} still queued")
    return done


def is_bjj(activity: dict, cfg: dict) -> bool:
    if activity.get("typeKey") in cfg["bjj_type_keys"]:
        return True
    name = (activity.get("activityName") or "").lower()
    return any(p in name for p in cfg["bjj_name_patterns"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="re-scan all history")
    parser.add_argument("--days", type=int, default=None, help="look-back window")
    args = parser.parse_args()

    cfg = json.loads((REPO_ROOT / "config.json").read_text())
    delay = float(cfg.get("request_delay_seconds", 0.8))

    activities_list = load_json(ACTIVITIES_FILE, [])
    activities = {a["activityId"]: a for a in activities_list}
    daily = load_json(DAILY_FILE, {})
    state = load_json(STATE_FILE, {})

    print("Connecting to Garmin Connect")
    client = connect()

    # Persist immediately: Garmin rotates the refresh token on use, so the copy we
    # just spent must be replaced before anything else can fail.
    if persist_token(client):
        print("  rolling token re-sealed to data/garmin_token.enc")
    else:
        print("  ! TOKEN_KEY not set — token not persisted (fine for a local run)")

    full = args.full or not activities
    try:
        if full:
            print("Backfilling complete activity history (first run)")
            raw = fetch_all_activities(client, delay)
        else:
            window = args.days or OVERLAP_DAYS
            print(f"Incremental sync, last {window} days")
            raw = fetch_recent_activities(client, window)

        added = updated = 0
        for item in raw:
            trimmed = trim(item)
            aid = trimmed["activityId"]
            if aid is None:
                continue
            if aid in activities:
                merged = {**activities[aid], **trimmed}
                if merged != activities[aid]:
                    activities[aid] = merged
                    updated += 1
            else:
                activities[aid] = trimmed
                added += 1
        print(f"  {added} new, {updated} updated, {len(activities)} total")

        bjj_ids = [aid for aid, a in activities.items() if is_bjj(a, cfg)]
        print(f"  {len(bjj_ids)} sessions match your BJJ rules")

        enrich_hr_zones(client, activities, bjj_ids, int(cfg["hr_zone_enrich_budget"]), delay)

        if activities:
            first = min(
                (a.get("startTimeLocal", "")[:10] for a in activities.values() if a.get("startTimeLocal")),
                default=date.today().isoformat(),
            )
            first_day = max(
                date.fromisoformat(first), date.today() - timedelta(days=365 * 6)
            )
            backfill_vo2max(client, daily, first_day, int(cfg["vo2max_backfill_budget"]), delay)

        state.update(
            {
                "last_sync_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "activity_count": len(activities),
                "bjj_count": len(bjj_ids),
                "last_error": None,
            }
        )
        ok = True
    except Exception as exc:  # noqa: BLE001 - always write what we have
        traceback.print_exc()
        state["last_error"] = f"{type(exc).__name__}: {exc}"
        state["last_error_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ok = False

    # A refresh part-way through the run rotates the token again, so re-seal.
    try:
        persist_token(client)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not re-seal token: {exc}")

    ordered = sorted(activities.values(), key=lambda a: a.get("startTimeLocal", ""))
    save_json(ACTIVITIES_FILE, ordered)
    save_json(DAILY_FILE, daily)
    save_json(STATE_FILE, state)
    print(f"Wrote {ACTIVITIES_FILE.name} ({len(ordered)} activities), "
          f"{DAILY_FILE.name} ({len(daily)} days)")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
