#!/usr/bin/env python3
"""Generate plausible fake history so the dashboard can be built and reviewed
before any Garmin credentials exist.

    python3 scripts/make_demo_data.py --years 4 --out data

Writes activities.json / daily_metrics.json / sync_state.json in the same shape
sync.py produces. Never run this against your real data directory once the live
sync is working.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def make(years: float, seed: int) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    today = date.today()
    start = today - timedelta(days=int(365.25 * years))

    activities: list[dict] = []
    daily: dict[str, dict] = {}
    aid = 12_000_000_000

    # Weekly rhythm: Mon/Wed gi, Fri no-gi, Sat open mat, with life getting in the way.
    slots = [
        (0, "19:00", "Gi — fundamentals", 90, 0.78),
        (2, "19:00", "Gi — advanced", 90, 0.70),
        (4, "18:00", "No-gi", 75, 0.55),
        (5, "11:00", "Open mat", 105, 0.35),
    ]

    cursor = start - timedelta(days=start.weekday())
    week_index = 0
    while cursor <= today:
        week_index += 1
        # Seasonal dips: summer holiday, Christmas, one injury layoff.
        month = cursor.month
        attendance = 1.0
        if month == 7:
            attendance = 0.35
        elif month == 12 and cursor.day > 18:
            attendance = 0.3
        if 40 < week_index < 46:  # a shoulder that needed six weeks off
            attendance = 0.05
        # Slow upward drift in commitment over the years.
        attendance *= 0.85 + 0.35 * min(1.0, week_index / 120)

        for weekday, clock, label, base_minutes, prob in slots:
            if rng.random() > prob * attendance:
                continue
            day = cursor + timedelta(days=weekday)
            if day > today or day < start:
                continue

            minutes = base_minutes + rng.randint(-12, 12)
            # Fitness improves: same class, slightly lower average heart rate.
            fitness = min(1.0, week_index / 150)
            avg_hr = int(rng.gauss(148 - 9 * fitness, 6))
            max_hr = avg_hr + rng.randint(24, 38)
            hard = "open mat" in label.lower() or "advanced" in label.lower()
            if hard:
                avg_hr += 6
                max_hr += 4

            secs = minutes * 60
            weights = [0.10, 0.22, 0.34, 0.24, 0.10] if hard else [0.16, 0.30, 0.33, 0.16, 0.05]
            jitter = [max(0.02, w + rng.uniform(-0.04, 0.04)) for w in weights]
            total_w = sum(jitter)
            zones = [round(secs * w / total_w, 1) for w in jitter]

            aid += rng.randint(1, 900)
            activities.append(
                {
                    "activityId": aid,
                    "activityName": label,
                    "startTimeLocal": f"{day.isoformat()} {clock}:00",
                    "typeKey": "mixed_martial_arts",
                    "parentTypeId": 29,
                    "duration": float(secs),
                    "elapsedDuration": float(secs + rng.randint(60, 400)),
                    "movingDuration": float(secs - rng.randint(0, 240)),
                    "calories": int(minutes * rng.uniform(7.5, 9.5)),
                    "averageHR": avg_hr,
                    "maxHR": min(196, max_hr),
                    "aerobicTrainingEffect": round(rng.uniform(2.4, 4.3), 1),
                    "anaerobicTrainingEffect": round(rng.uniform(0.4, 2.6), 1),
                    "activityTrainingLoad": round(minutes * rng.uniform(1.6, 2.6), 1),
                    **{f"hrTimeInZone_{i+1}": zones[i] for i in range(5)},
                }
            )

        # Strength and running, so "BJJ front and centre, rest in context" has context.
        for weekday, key, label, mins, prob in (
            (1, "strength_training", "Strength", 45, 0.45),
            (3, "running", "Easy run", 38, 0.30),
            (6, "running", "Long run", 62, 0.20),
        ):
            if rng.random() > prob * attendance:
                continue
            day = cursor + timedelta(days=weekday)
            if day > today or day < start:
                continue
            aid += rng.randint(1, 900)
            dur = (mins + rng.randint(-8, 8)) * 60
            activities.append(
                {
                    "activityId": aid,
                    "activityName": label,
                    "startTimeLocal": f"{day.isoformat()} 07:15:00",
                    "typeKey": key,
                    "parentTypeId": 17 if key == "running" else 29,
                    "duration": float(dur),
                    "calories": int(dur / 60 * rng.uniform(8, 12)),
                    "averageHR": int(rng.gauss(138 if key == "running" else 118, 7)),
                    "maxHR": int(rng.gauss(168 if key == "running" else 150, 8)),
                    "distance": round(dur / 60 * rng.uniform(160, 190), 1) if key == "running" else None,
                    "activityTrainingLoad": round(dur / 60 * rng.uniform(1.4, 2.2), 1),
                }
            )

        cursor += timedelta(days=7)

    # Weekly VO2 max samples with a gentle upward drift and noise.
    d = today
    i = 0
    while d >= start:
        progress = 1 - (today - d).days / max(1, (today - start).days)
        value = 42.0 + 4.5 * progress + math.sin(i / 6) * 0.6 + random.Random(seed + i).uniform(-0.4, 0.4)
        daily[d.isoformat()] = {"vo2max": round(value, 1)}
        d -= timedelta(days=7)
        i += 1

    activities = [{k: v for k, v in a.items() if v is not None} for a in activities]
    activities.sort(key=lambda a: a["startTimeLocal"])
    return activities, daily


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=4.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(REPO_ROOT / "data"))
    args = ap.parse_args()

    activities, daily = make(args.years, args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "activities.json").write_text(json.dumps(activities, indent=1))
    (out / "daily_metrics.json").write_text(json.dumps(daily, indent=1))
    (out / "sync_state.json").write_text(
        json.dumps(
            {
                "last_sync_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "activity_count": len(activities),
                "demo": True,
            },
            indent=1,
        )
    )
    bjj = sum(1 for a in activities if a["typeKey"] == "mixed_martial_arts")
    print(f"Wrote {len(activities)} demo activities ({bjj} BJJ) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
