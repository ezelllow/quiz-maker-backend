"""
Backfill `parent_attempt_id` on existing quiz_attempts rows.

Two attempts are considered the SAME quiz when their question sets match
(same multiset of question identifiers). Within each such group, the
OLDEST attempt is kept as the original (parent_attempt_id stays NULL)
and every later attempt is marked as a retake of it.

Usage:
    cd C:\\School\\quizMaker
    python dedupe_attempts.py

The script is dry-run by default: it prints what it would change and
asks for explicit confirmation before writing.
"""

import os
import sys
import json
import hashlib
from collections import defaultdict

try:
    from dotenv import load_dotenv
except ImportError:
    print("Missing dependency: python-dotenv. Run:  pip install python-dotenv")
    sys.exit(1)

try:
    import pymysql
except ImportError:
    print("Missing dependency: pymysql. Run:  pip install pymysql")
    sys.exit(1)


def fingerprint(questions_data):
    """Return a stable hash of the question set.

    Prefers stable IDs (qno / uid) when present; falls back to the
    stripped question text for legacy "skinny" rows.
    """
    if not isinstance(questions_data, list):
        return None
    ids = []
    for q in questions_data:
        if not isinstance(q, dict):
            continue
        key = q.get('qno') or q.get('uid')
        if key is None:
            text = (q.get('question_text') or '').strip()
            if not text:
                continue
            key = 'TEXT::' + text
        ids.append(str(key))
    if not ids:
        return None
    ids.sort()
    h = hashlib.sha1()
    for i in ids:
        h.update(i.encode('utf-8'))
        h.update(b'|')
    return h.hexdigest()


def main():
    load_dotenv()
    cfg = {
        "host": os.getenv("DB_HOST", "localhost"),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "quiz_maker"),
    }
    print(f"Connecting to MySQL at {cfg['host']} / database '{cfg['database']}'...")
    conn = pymysql.connect(**cfg, autocommit=False)
    cur = conn.cursor()

    # Ensure the parent_attempt_id column exists. The backend adds it on startup,
    # but the user may not have restarted yet — apply the migration here as well.
    cur.execute("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'quiz_attempts'
          AND COLUMN_NAME = 'parent_attempt_id'
    """)
    if cur.fetchone()[0] == 0:
        print("Schema migration: adding parent_attempt_id column to quiz_attempts...")
        cur.execute("""
            ALTER TABLE quiz_attempts
            ADD COLUMN parent_attempt_id INT NULL AFTER questions_data,
            ADD INDEX idx_parent_attempt_id (parent_attempt_id)
        """)
        conn.commit()
        print("Migration applied.")

    cur.execute("""
        SELECT id, user_id, attempted_at, parent_attempt_id, questions_data
        FROM quiz_attempts
        ORDER BY user_id, attempted_at ASC, id ASC
    """)
    rows = cur.fetchall()
    print(f"Found {len(rows)} total attempts across all users.")

    # Group by (user_id, fingerprint). Within a group the order is oldest-first
    # because of the ORDER BY above.
    groups = defaultdict(list)   # (user_id, fingerprint) -> [(id, attempted_at, current_parent), ...]
    unhashable = 0
    for attempt_id, user_id, attempted_at, parent, qjson in rows:
        try:
            questions = json.loads(qjson) if qjson else []
        except Exception:
            questions = []
        fp = fingerprint(questions)
        if fp is None:
            unhashable += 1
            continue
        groups[(user_id, fp)].append((attempt_id, attempted_at, parent))

    if unhashable:
        print(f"Skipped {unhashable} attempt(s) with no recoverable question identifiers.")

    # Compute updates: oldest in each group becomes the canonical parent;
    # everyone else gets parent_attempt_id = oldest.id, unless they already do.
    planned_updates = []  # (attempt_id, new_parent_id)
    planned_clears = []   # (attempt_id,)  # rows that should become the canonical original

    for (user_id, fp), members in groups.items():
        if len(members) < 2:
            # Singleton — ensure it's an original (no parent)
            (aid, _, parent) = members[0]
            if parent is not None:
                planned_clears.append((aid,))
            continue

        canonical_id = members[0][0]
        # Ensure canonical is parent_attempt_id IS NULL
        if members[0][2] is not None:
            planned_clears.append((canonical_id,))

        for (aid, _, parent) in members[1:]:
            if parent != canonical_id:
                planned_updates.append((aid, canonical_id))

    print()
    print(f"Distinct (user × quiz) groups: {len(groups)}")
    print(f"Attempts to re-parent as retakes : {len(planned_updates)}")
    print(f"Attempts to clear back to original: {len(planned_clears)}")

    if not planned_updates and not planned_clears:
        print("Nothing to do — every attempt is already correctly classified.")
        cur.close()
        conn.close()
        return

    # Show a small preview
    if planned_updates:
        print("\nSample of retakes that will be linked to their original:")
        for aid, pid in planned_updates[:10]:
            print(f"  attempt #{aid}  ->  parent #{pid}")
        if len(planned_updates) > 10:
            print(f"  ...and {len(planned_updates) - 10} more")

    print()
    confirm = input("Type APPLY (uppercase) to write these changes, anything else to cancel: ").strip()
    if confirm != "APPLY":
        print("Aborted — no changes made.")
        cur.close()
        conn.close()
        return

    try:
        if planned_clears:
            cur.executemany(
                "UPDATE quiz_attempts SET parent_attempt_id = NULL WHERE id = %s",
                planned_clears,
            )
        if planned_updates:
            cur.executemany(
                "UPDATE quiz_attempts SET parent_attempt_id = %s WHERE id = %s",
                [(pid, aid) for (aid, pid) in planned_updates],
            )
        conn.commit()
        print(f"Updated {len(planned_clears)} originals and {len(planned_updates)} retakes.")
    except Exception as e:
        conn.rollback()
        print(f"Error during update (rolled back): {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
