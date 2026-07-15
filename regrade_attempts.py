"""
Regrade every stored quiz attempt with the unified grader.

WHY: attempts were historically graded twice — the frontend computed the
attempt-level score/percentage, while the backend independently computed the
per-question is_correct flags with an OLDER normalizer (no PSLE "(n)" support,
no full-option-text ↔ letter resolution). The two disagreed, so the teacher
dashboard could show a question as ✓ while the review showed ✗ (or vice versa).

This script re-grades questions_data with the same grade_answer() logic now in
quiz_backend.py and rewrites is_correct / score / percentage so every stat on
the teacher dashboard is derived from ONE ground truth.

Rows from the old int-key-bug era (user_answer blanked on every question) are
UNRECOVERABLE — reported but left untouched.

Usage:
    cd C:\\School\\quizMaker
    python regrade_attempts.py            # dry run — reports what would change
    python regrade_attempts.py --apply    # actually writes the fixes
"""

import os
import re
import sys
import json

try:
    from dotenv import load_dotenv
    import pymysql
except ImportError as e:
    print(f"Missing dependency ({e}). Install with:  pip install python-dotenv pymysql")
    sys.exit(1)


# ── Grading — EXACT copies of quiz_backend.py grade_answer() ──────────────

def answer_key(val) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    m = re.match(r'^\((\d+)\)', s)
    if m:
        return m.group(1)
    m = re.match(r'^([A-Da-d])(?:[\.\)\s:\-]|$)', s)
    if m:
        return m.group(1).upper()
    return s.upper()


def _option_letter_and_body(line, idx):
    t = (line or "").strip()
    m = re.match(r'^\((\d+)\)\s*(.*)$', t)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r'^([A-Da-d])[\.\)\:\-]?\s+(.*)$', t)
    if m:
        return m.group(1).upper(), m.group(2)
    if re.fullmatch(r'[A-Da-d]', t):
        return t.upper(), ""
    return chr(65 + idx), t


def grade_answer(user_answer, correct_answer, options=None) -> bool:
    uk = answer_key(user_answer)
    ck = answer_key(correct_answer)
    if uk and ck and uk == ck:
        return True
    if not options or not uk or not ck:
        return False
    lines = [l.strip() for l in str(options).split('\n') if l.strip()]
    if not lines:
        return False

    def resolve(raw, key):
        s = str(raw or "").strip().upper()
        for i, line in enumerate(lines):
            letter, body = _option_letter_and_body(line, i)
            if s and (s == line.strip().upper() or (body and s == body.strip().upper())):
                return letter.upper()
            if key and key == answer_key(line):
                return letter.upper()
        return key

    ru = resolve(user_answer, uk)
    rc = resolve(correct_answer, ck)
    return bool(ru) and ru == rc


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    apply_changes = "--apply" in sys.argv

    load_dotenv()
    cfg = {
        "host":     os.getenv("DB_HOST", "localhost"),
        "port":     int(os.getenv("DB_PORT", "3306")),
        "user":     os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "quiz_maker"),
    }
    print(f"Connecting to MySQL at {cfg['host']}:{cfg['port']} / '{cfg['database']}'"
          f"  ({'APPLY' if apply_changes else 'DRY RUN'})\n")
    conn = pymysql.connect(**cfg, autocommit=False)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, user_id, score, percentage, total_questions, questions_data
        FROM quiz_attempts
        WHERE questions_data IS NOT NULL AND questions_data <> ''
        ORDER BY id
    """)
    rows = cur.fetchall()
    print(f"Scanning {len(rows)} attempts with stored questions_data...\n")

    fixed = flag_only = unrecoverable = untouched = parse_fail = 0

    for att_id, user_id, score, pct, total_q, qjson in rows:
        try:
            qs = json.loads(qjson)
        except Exception:
            parse_fail += 1
            continue
        if not isinstance(qs, list) or not qs:
            continue

        # Int-key-bug era: every user_answer blank → can't regrade.
        if all(not (q.get("user_answer") or "").strip() for q in qs if isinstance(q, dict)):
            unrecoverable += 1
            print(f"  ⚠️  attempt {att_id} (user {user_id}): user answers were never "
                  f"stored — per-question flags unrecoverable, skipping")
            continue

        flags_changed = 0
        new_score = 0
        for q in qs:
            if not isinstance(q, dict):
                continue
            correct_ans = (q.get("correct_answer") or q.get("answer") or "").strip()
            new_flag = grade_answer(q.get("user_answer"), correct_ans, q.get("options"))
            if bool(q.get("is_correct", False)) != new_flag:
                flags_changed += 1
            q["is_correct"] = new_flag
            new_score += int(new_flag)

        n = len(qs)
        new_pct = round((new_score / n) * 100) if n else 0
        score_changed = (int(score or 0) != new_score) or (int(pct or 0) != new_pct)

        if not flags_changed and not score_changed:
            untouched += 1
            continue

        print(f"  attempt {att_id} (user {user_id}): "
              f"{flags_changed} flag(s) regraded, "
              f"score {score}/{total_q} ({pct}%) → {new_score}/{n} ({new_pct}%)")

        if apply_changes:
            cur.execute(
                "UPDATE quiz_attempts SET questions_data = %s, score = %s, "
                "percentage = %s WHERE id = %s",
                (json.dumps(qs), new_score, new_pct, att_id),
            )
        if flags_changed and score_changed:
            fixed += 1
        else:
            flag_only += 1

    if apply_changes:
        conn.commit()
        print("\n✅ Changes committed.")
    else:
        conn.rollback()
        print("\nDry run only — re-run with --apply to write these fixes.")

    print(f"\nSummary: {fixed + flag_only} attempts corrected "
          f"({fixed} with score changes), {untouched} already consistent, "
          f"{unrecoverable} unrecoverable (blank answers), {parse_fail} unparseable.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
