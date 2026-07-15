"""
Diagnose + repair the daily/weekly leaderboard XP ledger.

WHY: the daily/weekly leaderboards rank by SUM(daily_challenges.xp), a per-day
XP ledger written at quiz-submit time. Until 2026-07-15 that write was a plain
UPDATE, which silently no-ops when the day's row doesn't exist — the XP went
into users.xp (all-time board correct) but vanished from daily/weekly. XP
earned before the ledger existed was also never banked. This script:

  1. DIAGNOSE (always): per user, compares lifetime users.xp against
     SUM(daily_challenges.xp) so you can SEE the drift.
  2. REPAIR (--apply): rebuilds each day's ledger from quiz_attempts — for
     every user+day, recomputes base+perfect XP from that day's DAILY attempts
     (same formula as the backend) and raises daily_challenges.xp to at least
     that value. Never lowers an existing value; daily-goal/streak bonuses
     can't be reconstructed, so repaired days are a slight UNDER-estimate.

If your MySQL server stores attempted_at in UTC (e.g. Railway), pass
--tz-offset 8 so attempts group into Singapore days. A local SG machine
storing local time needs no offset.

Usage:
    python repair_leaderboard_xp.py                 # diagnose only
    python repair_leaderboard_xp.py --apply         # write repairs
    python repair_leaderboard_xp.py --apply --tz-offset 8
"""

import os
import sys

try:
    from dotenv import load_dotenv
    import pymysql
except ImportError as e:
    print(f"Missing dependency ({e}). Install with:  pip install python-dotenv pymysql")
    sys.exit(1)

# Must mirror quiz_backend.py xp_for_quiz()
XP_BASE_PER_CORRECT     = 10
XP_DIFFICULTY_MULT      = {"easy": 1.0, "medium": 1.25, "hard": 1.5}
XP_BONUS_PERFECT        = 20
XP_BONUS_PERFECT_MIN_QS = 3


def xp_base_perfect(correct, total, difficulty):
    correct = max(0, int(correct or 0))
    total = max(0, int(total or 0))
    mult = XP_DIFFICULTY_MULT.get((difficulty or "").strip().lower(), 1.0)
    base = int(round(correct * XP_BASE_PER_CORRECT * mult))
    perfect = XP_BONUS_PERFECT if (total >= XP_BONUS_PERFECT_MIN_QS and correct == total and correct > 0) else 0
    return base + perfect


def main():
    apply_changes = "--apply" in sys.argv
    tz_offset = 0
    if "--tz-offset" in sys.argv:
        tz_offset = int(sys.argv[sys.argv.index("--tz-offset") + 1])

    load_dotenv()
    conn = pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "quiz_maker"),
        autocommit=False,
    )
    cur = conn.cursor()
    mode = "APPLY" if apply_changes else "DRY RUN"
    print(f"Connected ({mode}, tz-offset {tz_offset}h)\n")

    # ── 1. Diagnose: lifetime XP vs banked ledger XP ─────────────────────
    cur.execute("""
        SELECT u.id, u.name, COALESCE(u.xp, 0) AS lifetime,
               COALESCE((SELECT SUM(dc.xp) FROM daily_challenges dc
                         WHERE dc.user_id = u.id), 0) AS banked
        FROM users u
        WHERE COALESCE(u.xp, 0) > 0
           OR EXISTS (SELECT 1 FROM daily_challenges dc WHERE dc.user_id = u.id)
        ORDER BY lifetime DESC
    """)
    print(f"{'user':<24} {'lifetime xp':>12} {'banked/day xp':>14} {'drift':>8}")
    drift_total = 0
    for uid, name, lifetime, banked in cur.fetchall():
        drift = int(lifetime) - int(banked)
        drift_total += max(0, drift)
        flag = "  <-- missing from daily/weekly boards" if drift > 0 else ""
        print(f"{(name or f'#{uid}')[:24]:<24} {int(lifetime):>12} {int(banked):>14} {drift:>8}{flag}")
    print(f"\nTotal XP missing from the per-day ledger: {drift_total}\n")

    # ── 2. Repair: rebuild per-day XP floor from daily quiz_attempts ─────
    cur.execute(f"""
        SELECT user_id,
               DATE(DATE_ADD(attempted_at, INTERVAL %s HOUR)) AS day,
               score, total_questions, difficulty
        FROM quiz_attempts
        WHERE COALESCE(quiz_type, 'practice') = 'daily'
        ORDER BY user_id, day
    """, (tz_offset,))
    per_day = {}
    for uid, day, score, total_q, diff in cur.fetchall():
        per_day.setdefault((uid, day), 0)
        per_day[(uid, day)] += xp_base_perfect(score, total_q, diff)

    repaired = skipped = 0
    for (uid, day), computed in sorted(per_day.items()):
        cur.execute(
            "SELECT xp FROM daily_challenges WHERE user_id = %s "
            "AND subject = 'Physics' AND challenge_date = %s",
            (uid, day),
        )
        row = cur.fetchone()
        current = int(row[0]) if row else None
        if current is not None and current >= computed:
            skipped += 1
            continue
        repaired += 1
        print(f"  user {uid} {day}: banked {current if current is not None else '(no row)'} "
              f"-> {computed}")
        if apply_changes:
            if row:
                cur.execute(
                    "UPDATE daily_challenges SET xp = %s WHERE user_id = %s "
                    "AND subject = 'Physics' AND challenge_date = %s",
                    (computed, uid, day),
                )
            else:
                cur.execute(
                    "INSERT INTO daily_challenges (user_id, subject, challenge_date, "
                    "score, total, percentage, passed, attempts, xp) "
                    "VALUES (%s, 'Physics', %s, 0, 0, 0, FALSE, 0, %s)",
                    (uid, day, computed),
                )

    if apply_changes:
        conn.commit()
        print(f"\n✅ Committed: {repaired} user-days repaired, {skipped} already correct.")
    else:
        conn.rollback()
        print(f"\nDry run: {repaired} user-days would be repaired, {skipped} already correct.")
        print("Re-run with --apply to write.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
