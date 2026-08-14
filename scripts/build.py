#!/usr/bin/env python3
"""Turn data/*.json into the dashboard at docs/index.html.

Everything the page needs is computed here and embedded as one JSON blob, so the
published dashboard is a single self-contained file with no fetches and no CDN.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TEMPLATE = REPO_ROOT / "web" / "template.html"
OUTPUT = REPO_ROOT / "docs" / "index.html"

HEATMAP_DAYS = 371  # 53 whole weeks


# ─────────────────────────────────────────────────────────── helpers ──


def load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def parse_local(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(value[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        return None


def is_bjj(activity: dict, cfg: dict) -> bool:
    if activity.get("typeKey") in cfg["bjj_type_keys"]:
        return True
    name = (activity.get("activityName") or "").lower()
    return any(p in name for p in cfg["bjj_name_patterns"])


def pretty_sport(type_key: str) -> str:
    special = {
        "mixed_martial_arts": "BJJ",
        "strength_training": "Strength",
        "indoor_cardio": "Cardio",
        "cardio_training": "Cardio",
        "treadmill_running": "Running",
        "indoor_cycling": "Cycling",
        "lap_swimming": "Swimming",
        "open_water_swimming": "Swimming",
        "hiit": "HIIT",
    }
    if type_key in special:
        return special[type_key]
    return type_key.replace("_", " ").title()


# ────────────────────────────────────────────────────────── sessions ──


def build_sessions(activities: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """Return (bjj_sessions, other_sessions), both cleaned and date-sorted."""
    lo = float(cfg["min_session_minutes"])
    hi = float(cfg["max_session_minutes"])
    bjj: list[dict] = []
    other: list[dict] = []

    for a in activities:
        started = parse_local(a.get("startTimeLocal"))
        if not started:
            continue
        raw_minutes = float(a.get("duration") or 0) / 60.0
        if raw_minutes <= 0:
            continue
        minutes = min(raw_minutes, hi)

        zones = [float(a.get(f"hrTimeInZone_{i}") or 0) for i in range(1, 6)]
        record = {
            "id": a.get("activityId"),
            "date": started.date().isoformat(),
            "time": started.strftime("%H:%M"),
            "name": (a.get("activityName") or "").strip(),
            "sport": pretty_sport(a.get("typeKey", "unknown")),
            "typeKey": a.get("typeKey", "unknown"),
            "minutes": round(minutes, 1),
            "avgHR": a.get("averageHR"),
            "maxHR": a.get("maxHR"),
            "calories": a.get("calories"),
            "load": a.get("activityTrainingLoad"),
            "aerobicTE": a.get("aerobicTrainingEffect"),
            "anaerobicTE": a.get("anaerobicTrainingEffect"),
            "zones": [round(z, 1) for z in zones] if sum(zones) > 0 else None,
        }
        if is_bjj(a, cfg):
            if raw_minutes < lo:
                continue  # too short to be a real class
            bjj.append(record)
        else:
            other.append(record)

    bjj.sort(key=lambda s: (s["date"], s["time"]))
    other.sort(key=lambda s: (s["date"], s["time"]))
    return bjj, other


# ──────────────────────────────────────────────────────── aggregates ──


def weekly_series(sessions: list[dict]) -> list[dict]:
    buckets: dict[date, dict] = defaultdict(lambda: {"sessions": 0, "minutes": 0.0})
    for s in sessions:
        wk = monday(date.fromisoformat(s["date"]))
        buckets[wk]["sessions"] += 1
        buckets[wk]["minutes"] += s["minutes"]
    if not buckets:
        return []
    cursor = min(buckets)
    end = monday(date.today())
    out = []
    while cursor <= end:
        b = buckets.get(cursor, {"sessions": 0, "minutes": 0.0})
        out.append(
            {
                "week": cursor.isoformat(),
                "sessions": b["sessions"],
                "hours": round(b["minutes"] / 60.0, 2),
            }
        )
        cursor += timedelta(days=7)
    return out


def monthly_series(sessions: list[dict]) -> list[dict]:
    buckets: dict[str, dict] = defaultdict(lambda: {"sessions": 0, "minutes": 0.0})
    for s in sessions:
        key = s["date"][:7]
        buckets[key]["sessions"] += 1
        buckets[key]["minutes"] += s["minutes"]
    if not buckets:
        return []
    first = min(buckets)
    y, m = int(first[:4]), int(first[5:7])
    today = date.today()
    out = []
    while (y, m) <= (today.year, today.month):
        key = f"{y:04d}-{m:02d}"
        b = buckets.get(key, {"sessions": 0, "minutes": 0.0})
        out.append(
            {
                "month": key,
                "sessions": b["sessions"],
                "hours": round(b["minutes"] / 60.0, 2),
            }
        )
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def heatmap_series(sessions: list[dict]) -> list[dict]:
    by_day: dict[str, dict] = defaultdict(lambda: {"minutes": 0.0, "sessions": 0})
    for s in sessions:
        by_day[s["date"]]["minutes"] += s["minutes"]
        by_day[s["date"]]["sessions"] += 1

    end = date.today()
    # Finish on the Sunday of the current week so columns are whole weeks.
    end += timedelta(days=(6 - end.weekday()))
    start = monday(end - timedelta(days=HEATMAP_DAYS - 1))
    out = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        d = by_day.get(key)
        out.append(
            {
                "date": key,
                "minutes": round(d["minutes"], 1) if d else 0,
                "sessions": d["sessions"] if d else 0,
                "future": cursor > date.today(),
            }
        )
        cursor += timedelta(days=1)
    return out


def rolling_load(sessions: list[dict]) -> list[dict]:
    """Acute (7-day) vs chronic (28-day) load, both as minutes-per-week."""
    if not sessions:
        return []
    by_day: dict[date, float] = defaultdict(float)
    for s in sessions:
        by_day[date.fromisoformat(s["date"])] += s["minutes"]

    start = min(by_day)
    end = date.today()
    if (end - start).days > 365 * 6:
        start = end - timedelta(days=365 * 6)

    out = []
    cursor = start
    while cursor <= end:
        acute = sum(by_day.get(cursor - timedelta(days=i), 0.0) for i in range(7))
        chronic = sum(by_day.get(cursor - timedelta(days=i), 0.0) for i in range(28)) / 4.0
        out.append(
            {
                "date": cursor.isoformat(),
                "acute": round(acute, 1),
                "chronic": round(chronic, 1),
            }
        )
        cursor += timedelta(days=1)
    return out


def zones_by_month(sessions: list[dict]) -> list[dict]:
    buckets: dict[str, list[float]] = defaultdict(lambda: [0.0] * 5)
    covered: dict[str, int] = defaultdict(int)
    for s in sessions:
        if not s["zones"]:
            continue
        key = s["date"][:7]
        for i, secs in enumerate(s["zones"]):
            buckets[key][i] += secs
        covered[key] += 1
    out = []
    for key in sorted(buckets):
        total = sum(buckets[key])
        if total <= 0:
            continue
        out.append(
            {
                "month": key,
                "sessions": covered[key],
                "minutes": [round(v / 60.0, 1) for v in buckets[key]],
                "share": [round(100 * v / total, 1) for v in buckets[key]],
            }
        )
    return out


def streaks(weekly: list[dict], target: int) -> dict:
    """Streaks in weeks. The current (incomplete) week never breaks a streak."""
    if not weekly:
        return {"current": 0, "longest": 0, "targetHitLast12": 0,
                "targetWindow": 0, "targetStreak": 0}

    completed = weekly[:-1]
    longest = run = 0
    for w in completed:
        run = run + 1 if w["sessions"] > 0 else 0
        longest = max(longest, run)

    current = 0
    for w in reversed(completed):
        if w["sessions"] > 0:
            current += 1
        else:
            break
    if weekly[-1]["sessions"] > 0:
        current += 1
        longest = max(longest, current)

    target_streak = 0
    for w in reversed(completed):
        if w["sessions"] >= target:
            target_streak += 1
        else:
            break
    if weekly[-1]["sessions"] >= target:
        target_streak += 1

    last12 = completed[-12:] if len(completed) >= 12 else completed
    hit = sum(1 for w in last12 if w["sessions"] >= target)

    return {
        "current": current,
        "longest": longest,
        "targetHitLast12": hit,
        "targetWindow": len(last12),
        "targetStreak": target_streak,
    }


def sport_mix(bjj: list[dict], other: list[dict], days: int = 365) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    totals: dict[str, float] = defaultdict(float)
    for s in bjj:
        if s["date"] >= cutoff:
            totals["BJJ"] += s["minutes"]
    for s in other:
        if s["date"] >= cutoff:
            totals[s["sport"]] += s["minutes"]
    if not totals:
        return []
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    bjj_hours = totals.get("BJJ", 0.0) / 60.0
    rest = [(k, v) for k, v in ranked if k != "BJJ"]
    keep, tail = rest[:4], rest[4:]
    out = [{"sport": "BJJ", "hours": round(bjj_hours, 1)}] if bjj_hours else []
    out += [{"sport": k, "hours": round(v / 60.0, 1)} for k, v in keep]
    if tail:
        out.append({"sport": "Other", "hours": round(sum(v for _, v in tail) / 60.0, 1)})
    return [s for s in out if s["hours"] > 0]


def intensity_points(sessions: list[dict]) -> list[dict]:
    pts = [
        {"date": s["date"], "avgHR": s["avgHR"], "minutes": s["minutes"], "name": s["name"]}
        for s in sessions
        if s.get("avgHR")
    ]
    # 8-session trailing mean, so the trend line is readable through the scatter.
    window: list[float] = []
    for p in pts:
        window.append(float(p["avgHR"]))
        if len(window) > 8:
            window.pop(0)
        p["trend"] = round(statistics.fmean(window), 1)
    return pts


def vo2max_series(daily: dict) -> list[dict]:
    out = [
        {"date": k, "value": v["vo2max"]}
        for k, v in sorted(daily.items())
        if isinstance(v, dict) and v.get("vo2max")
    ]
    return out


# ───────────────────────────────────────────────────────────── build ──


def main() -> int:
    cfg = json.loads((REPO_ROOT / "config.json").read_text())
    activities = load(DATA_DIR / "activities.json", [])
    daily = load(DATA_DIR / "daily_metrics.json", {})
    state = load(DATA_DIR / "sync_state.json", {})

    bjj, other = build_sessions(activities, cfg)
    weekly = weekly_series(bjj)
    monthly = monthly_series(bjj)

    total_minutes = sum(s["minutes"] for s in bjj)
    today = date.today()
    week_start = monday(today)
    this_week = [s for s in bjj if date.fromisoformat(s["date"]) >= week_start]
    this_month = [s for s in bjj if s["date"][:7] == month_key(today)]

    days_in_month = (date(today.year + (today.month // 12), (today.month % 12) + 1, 1) - timedelta(days=1)).day
    month_hours = sum(s["minutes"] for s in this_month) / 60.0
    month_target = float(cfg["monthly_hours_target"])
    pace_target = month_target * today.day / days_in_month
    projected = month_hours * days_in_month / today.day if today.day else 0.0

    first_date = bjj[0]["date"] if bjj else None
    span_weeks = 0.0
    if first_date:
        span_weeks = max(1.0, (today - date.fromisoformat(first_date)).days / 7.0)

    recent_hr = [s["avgHR"] for s in bjj[-20:] if s.get("avgHR")]
    baseline_hr = [s["avgHR"] for s in bjj[-120:-20] if s.get("avgHR")]

    last = bjj[-1] if bjj else None
    days_since = (today - date.fromisoformat(last["date"])).days if last else None

    payload = {
        "meta": {
            "athlete": cfg.get("athlete_name", ""),
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "lastSync": state.get("last_sync_utc"),
            "syncError": state.get("last_error"),
            "weeklyTarget": int(cfg["weekly_session_target"]),
            "monthlyTarget": month_target,
            "totalActivities": len(activities),
            "zonesPending": sum(1 for s in bjj if not s["zones"]),
        },
        "lifetime": {
            "hours": round(total_minutes / 60.0, 1),
            "sessions": len(bjj),
            "firstSession": first_date,
            "years": round((today - date.fromisoformat(first_date)).days / 365.25, 1) if first_date else 0,
            "avgPerWeek": round(len(bjj) / span_weeks, 2) if span_weeks else 0,
            "avgSessionMinutes": round(total_minutes / len(bjj), 0) if bjj else 0,
            "calories": int(sum(s["calories"] or 0 for s in bjj)),
            "mostInAWeek": max((w["sessions"] for w in weekly), default=0),
        },
        "now": {
            "weekStart": week_start.isoformat(),
            "weekSessions": len(this_week),
            "weekHours": round(sum(s["minutes"] for s in this_week) / 60.0, 1),
            "monthHours": round(month_hours, 1),
            "monthPaceTarget": round(pace_target, 1),
            "monthProjected": round(projected, 1),
            "monthSessions": len(this_month),
            "dayOfMonth": today.day,
            "daysInMonth": days_in_month,
            "daysSinceLast": days_since,
            "lastSession": last,
            "avgHRRecent": round(statistics.fmean(recent_hr)) if recent_hr else None,
            "avgHRBaseline": round(statistics.fmean(baseline_hr)) if baseline_hr else None,
        },
        "streaks": streaks(weekly, int(cfg["weekly_session_target"])),
        "weekly": weekly,
        "monthly": monthly,
        "heatmap": heatmap_series(bjj),
        "load": rolling_load(bjj),
        "zones": zones_by_month(bjj),
        "intensity": intensity_points(bjj),
        "vo2max": vo2max_series(daily),
        "sportMix": sport_mix(bjj, other),
        "sessions": bjj[-400:],
    }

    template = TEMPLATE.read_text()
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # </script> inside embedded JSON would close the tag early.
    blob = blob.replace("</", "<\\/")
    html = template.replace("/*__DASHBOARD_DATA__*/null", blob)
    if "/*__DASHBOARD_DATA__*/" in template and blob not in html:
        raise SystemExit("Failed to inject data into the template")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html)
    (OUTPUT.parent / ".nojekyll").write_text("")

    size_kb = OUTPUT.stat().st_size / 1024
    print(
        f"Built {OUTPUT.relative_to(REPO_ROOT)} — {len(bjj)} BJJ sessions, "
        f"{payload['lifetime']['hours']}h lifetime, {size_kb:.0f} KB"
    )
    if payload["meta"]["zonesPending"]:
        print(f"  {payload['meta']['zonesPending']} sessions still awaiting HR zones")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
