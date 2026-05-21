"""
Diagnostic: inspect what is actually stored in quiz_attempts.questions_data.

This tells us WHY the stats dashboard shows 0% on every breakdown even though
overall accuracy looks right.

  - Overall accuracy on the dashboard is computed from the attempt-level `score`
    column (frontend-trusted).
  - Every per-topic / per-difficulty / per-subject breakdown is computed from the
    per-question `is_correct` flag baked into questions_data at submit time.

If the per-question flags are all False while `score` says otherwise, those rows
were submitted by a backend that had the int-key bug. That data cannot be
recomputed (user_answer was also blanked), so those breakdowns will show 0%
forever — only NEW quizzes on the fixed backend will aggregate correctly.

Usage:
    cd C:\\School\\quizMaker
    python diagnose_stats.py
"""

import os
import sys
import json

try:
    from dotenv import load_dotenv
    import pymysql
except ImportError as e:
    print(f"Missing dependency ({e}). Install with:  pip install python-dotenv pymysql")
    sys.exit(1)


def main():
    load_dotenv()
    cfg = {
        "host": os.getenv("DB_HOST", "localhost"),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "quiz_maker"),
    }
    print(f"Connecting to MySQL at {cfg['host']} / database '{cfg['database']}'...\n")
    conn = pymysql.connect(**cfg, autocommit=False)
    cur = conn.cursor()

    # Pick the user
    cur.execute(
        """
        SELECT u.id, u.email, u.name, COUNT(qa.id)
        FROM users u
        LEFT JOIN quiz_attempts qa ON qa.user_id = u.id
        GROUP BY u.id, u.email, u.name
        ORDER BY COUNT(qa.id) DESC, u.id
        """
    )
    users = cur.fetchall()
    if not users:
        print("No users found.")
        return
    print(f"{'id':<5} {'email':<40} {'name':<20} attempts")
    print("-" * 75)
    for r in users:
        print(f"{r[0]:<5} {(r[1] or ''):<40} {(r[2] or ''):<20} {r[3]}")
    print()

    target = input("Enter the user id to inspect (or 'q' to quit): ").strip()
    if not target.isdigit():
        print("Aborted.")
        return
    user_id = int(target)

    cur.execute(
        """
        SELECT id, quiz_type, score, total_questions, percentage,
               questions_data, attempted_at
        FROM quiz_attempts
        WHERE user_id = %s
        ORDER BY attempted_at ASC
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        print(f"No attempts for user_id={user_id}.")
        return

    print(f"\n{'='*78}")
    print(f"Found {len(rows)} attempt(s) for user_id={user_id}\n")

    grand_q = 0
    grand_flag_true = 0
    grand_blank_answer = 0
    practice_q = 0
    practice_flag_true = 0

    for (aid, qtype, score, total, pct, qjson, ts) in rows:
        try:
            questions = json.loads(qjson) if qjson else []
        except Exception:
            questions = []
        valid = [q for q in questions if isinstance(q, dict)]

        flag_true = sum(1 for q in valid if q.get("is_correct"))
        blank_ans = sum(1 for q in valid if not (q.get("user_answer") or "").strip())

        grand_q += len(valid)
        grand_flag_true += flag_true
        grand_blank_answer += blank_ans
        if qtype == "practice":
            practice_q += len(valid)
            practice_flag_true += flag_true

        verdict = ""
        if valid and flag_true == 0 and (score or 0) > 0:
            verdict = "  <-- CORRUPTED: score says correct, per-question flags all False"
        elif valid and flag_true == score:
            verdict = "  <-- OK: flags match score"
        elif not valid:
            verdict = "  <-- legacy skinny row (no per-question data)"

        print(f"Attempt #{aid}  type={qtype or '?':<9} score={score}/{total}  "
              f"pct={pct}%  stored_questions={len(valid)}  "
              f"is_correct=True x{flag_true}  blank_user_answer x{blank_ans}{verdict}")

        # Show first 3 questions of each attempt in detail
        for i, q in enumerate(valid[:3]):
            print(f"     q{i}: is_correct={str(q.get('is_correct')):<5} "
                  f"user_answer={repr(q.get('user_answer'))[:18]:<20} "
                  f"correct_answer={repr(q.get('correct_answer'))[:18]:<20} "
                  f"subtopic={repr(q.get('subtopic'))[:24]}")
        print()

    print("=" * 78)
    print("SUMMARY")
    print(f"  All attempts : {grand_q} stored questions, "
          f"{grand_flag_true} with is_correct=True, "
          f"{grand_blank_answer} with blank user_answer")
    print(f"  Practice only: {practice_q} stored questions, "
          f"{practice_flag_true} with is_correct=True   "
          f"(the dashboard only reads practice attempts)")
    print()
    if practice_q and practice_flag_true == 0:
        print("  DIAGNOSIS: every practice question has is_correct=False.")
        print("  These rows were saved by the buggy backend. The breakdowns")
        print("  CANNOT be recomputed (user_answer was blanked too).")
        print("  Fix is verified in code -> take a NEW practice quiz on the")
        print("  restarted backend and the breakdowns will populate.")
    elif practice_flag_true > 0:
        print("  DIAGNOSIS: some practice questions DO have is_correct=True.")
        print("  The fixed backend is working. If the dashboard still shows 0%,")
        print("  the issue is frontend-side aggregation, not the stored data.")
    print("=" * 78)


if __name__ == "__main__":
    main()
