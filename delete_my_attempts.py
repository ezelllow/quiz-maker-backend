"""
One-shot cleanup script: delete all quiz attempts (saved quizzes) for a chosen account.

Usage:
    cd C:\\School\\quizMaker
    python delete_my_attempts.py

It will:
  1. Load DB credentials from .env (same config the backend uses)
  2. List users with their current attempt counts
  3. Ask which user (by id)
  4. Show the count and require typing DELETE to confirm
  5. Delete those rows from quiz_attempts (and quiz_question_history if present)

This is a manual maintenance script — not exposed as an API endpoint.
"""

import os
import sys

try:
    from dotenv import load_dotenv
except ImportError:
    print("Missing dependency: python-dotenv. Install with:  pip install python-dotenv")
    sys.exit(1)

try:
    import pymysql
except ImportError:
    print("Missing dependency: pymysql. Install with:  pip install pymysql")
    sys.exit(1)


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

    # 1. List users with attempt counts
    cur.execute(
        """
        SELECT u.id, u.email, u.name, COUNT(qa.id) AS attempts
        FROM users u
        LEFT JOIN quiz_attempts qa ON qa.user_id = u.id
        GROUP BY u.id, u.email, u.name
        ORDER BY attempts DESC, u.id
        """
    )
    rows = cur.fetchall()
    if not rows:
        print("No users found.")
        return

    print()
    print(f"{'id':<5} {'email':<40} {'name':<20} attempts")
    print("-" * 75)
    for r in rows:
        print(f"{r[0]:<5} {(r[1] or ''):<40} {(r[2] or ''):<20} {r[3]}")
    print()

    # 2. Pick a user
    try:
        target = input("Enter the user id to wipe attempts for (or 'q' to quit): ").strip()
    except EOFError:
        print("No input given. Aborting.")
        return
    if target.lower() in ("q", "quit", "exit", ""):
        print("Aborted.")
        return
    if not target.isdigit():
        print("Not a valid id. Aborting.")
        return

    user_id = int(target)
    match = next((r for r in rows if r[0] == user_id), None)
    if not match:
        print(f"No user with id={user_id}. Aborting.")
        return

    _, email, name, attempts = match
    if attempts == 0:
        print(f"User '{email}' has no attempts. Nothing to delete.")
        return

    # 3. Confirm
    print()
    print(f"You are about to permanently delete {attempts} saved quiz attempt(s) for:")
    print(f"    id={user_id}  email={email}  name={name}")
    print()
    confirm = input("Type DELETE (uppercase) to confirm, anything else to cancel: ").strip()
    if confirm != "DELETE":
        print("Not confirmed. Aborting — no changes made.")
        return

    # 4. Delete. Also clean up any auxiliary per-question history table if it exists.
    try:
        # Optional auxiliary table: quiz_question_history (may not exist in all schemas)
        cur.execute("SHOW TABLES LIKE 'quiz_question_history'")
        if cur.fetchone():
            cur.execute("DELETE FROM quiz_question_history WHERE user_id = %s", (user_id,))
            print(f"Deleted {cur.rowcount} row(s) from quiz_question_history.")

        cur.execute("DELETE FROM quiz_attempts WHERE user_id = %s", (user_id,))
        deleted = cur.rowcount
        conn.commit()
        print(f"Deleted {deleted} row(s) from quiz_attempts for user_id={user_id}.")
        print("Done.")
    except Exception as e:
        conn.rollback()
        print(f"Error during delete (rolled back): {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
