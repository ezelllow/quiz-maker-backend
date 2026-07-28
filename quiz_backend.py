"""
HabitGo Backend - FastAPI
Fetches questions from Google Sheet, images from Google Drive
Returns filtered quizzes based on difficulty, subtopic, and count
"""

import os
import random
import re
import time
from typing import List, Optional, Tuple, Dict
from collections import defaultdict, OrderedDict
import threading
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from io import BytesIO
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import json
import queue
from functools import lru_cache
import pymysql
import jwt
from datetime import datetime, timedelta
from dotenv import load_dotenv
from passlib.context import CryptContext
from google.auth.transport import requests
from google.oauth2 import id_token

# Load environment variables from .env file
load_dotenv()

# ============================================================================
# CONFIGURATION
# ============================================================================

SPREADSHEET_ID = '1TOmLo9UNpzOggeX27j1p6Q2NdAnCWpRJ1ErYAEJ-sZU'
# P6 Math lives in its OWN spreadsheet (not a tab of the main workbook). Its
# rows are appended to the question bank at load with Level FORCED to P6Math.
P6_MATH_SPREADSHEET_ID = os.getenv('P6_MATH_SPREADSHEET_ID',
                                   '1ND9K9_m8BlOBlXqUi5omqMiGqKstUB8mDnrmmGyRrAY')
P6_MATH_LEVEL = 'P6Math'
P6_MATH_TAB_LABEL = 'P6 Math [external]'
QUESTION_FOLDER_ID = '10TtAVgxTsczSFxIrkwSSy_KFQlebWCiX'
# Root Drive folder(s) scanned for question images. The scan is RECURSIVE, so
# images resolve whether they sit directly in a folder (old flat layout) or in
# per-paper subfolders (new layout). If the Pure / Combined physics images live
# in their own separate Drive folders, add those folder IDs here via the
# QUESTION_FOLDER_IDS env var (comma-separated). Defaults to QUESTION_FOLDER_ID.
_DEFAULT_QUESTION_FOLDER_IDS = ','.join([
    QUESTION_FOLDER_ID,                      # legacy flat folder (un-migrated papers)
    '1c3e88WMHQ62uG1AZ4VDeK_d5tMwPwFqO',     # pure_physics_p1            (Level "Pure Physics")
    '1IH-v6RCDsEnYm8oeJS7RaIhSicHhNFcC',     # combined_physics_p1_G3     (Level "combinedG3")
    '154YP-TOlk6gFgVS6e9Hegr60CE26OWDl',     # combined_physics_p1_G2     (Level "combinedG2")
    '1RkICWBLlBpV0k87NZzRFTifpL-evTwDw',     # combined_physics_p1_G1     (Level "combinedG1")
    '1o9w7cT6Ge1tn8RY2qKuAsG_-W_yvTVHF',     # p6_math                    (Level "P6Math")
])
QUESTION_FOLDER_IDS = [
    fid.strip()
    for fid in (os.getenv('QUESTION_FOLDER_IDS') or _DEFAULT_QUESTION_FOLDER_IDS).split(',')
    if fid.strip()
]
# Sheet tab names to read questions from. The workbook now splits Pure vs
# 4E5N into separate tabs (it used to be one tab called 'Physics'). Override
# with the SHEET_NAMES env var ("4E5N,Pure Physics") if you rename tabs.
# SHEET_NAME (singular) is still honoured for backward compatibility.
SHEET_NAMES = [
    name.strip()
    for name in (os.getenv('SHEET_NAMES') or os.getenv('SHEET_NAME') or 'Pure Physics,combinedG1,combinedG2,combinedG3').split(',')
    if name.strip()
]

# How long the in-memory question bank is trusted before the Sheet is checked
# for changes again (stale-while-revalidate: the check runs in a background
# thread, requests are never blocked on it). Set 0 to disable auto-refresh.
QUESTIONS_CACHE_TTL_SECONDS = int(os.getenv('QUESTIONS_CACHE_TTL_SECONDS', '300'))
# Optional shared secret for POST /api/admin/refresh (?key=... or X-Refresh-Key
# header). When unset, the endpoint requires a teacher JWT instead.
ADMIN_REFRESH_KEY = os.getenv('ADMIN_REFRESH_KEY', '').strip()

# Google API Scopes
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

# ============================================================================
# AUTHENTICATION CONFIGURATION
# ============================================================================

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT Configuration
JWT_SECRET = os.getenv("JWT_SECRET", "change_this_secret_key_in_production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 720  # 30 days — long sessions during dev; avoids frequent re-logins

# MySQL Configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'quiz_maker'),
    'port': int(os.getenv('DB_PORT', '3306')),
}

# Public base URL — used to build absolute image URLs returned to the frontend.
# In dev: defaults to http://localhost:8000. In production set to your Render URL.
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', 'http://localhost:8000').rstrip('/')

# Google OAuth
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')

# ============================================================================
# INITIALIZE GOOGLE APIS
# ============================================================================

def get_credentials():
    """
    Load credentials from environment or service account file.

    Option 1: Set GOOGLE_SERVICE_ACCOUNT_JSON env variable to the FULL JSON contents
             (preferred for hosted deploys like Render — paste the file body).
    Option 2: Set GOOGLE_APPLICATION_CREDENTIALS env variable to a JSON file path.
    Option 3: Place credentials.json in the same directory as this script.
    """
    inline_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if inline_json:
        info = json.loads(inline_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    elif os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
        creds = Credentials.from_service_account_file(
            os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'),
            scopes=SCOPES
        )
    elif os.path.exists('credentials.json'):
        creds = Credentials.from_service_account_file(
            'credentials.json',
            scopes=SCOPES
        )
    else:
        raise FileNotFoundError(
            "Credentials not found! Set GOOGLE_SERVICE_ACCOUNT_JSON (preferred) or "
            "GOOGLE_APPLICATION_CREDENTIALS env variable, or place credentials.json "
            "in the script directory."
        )
    return creds

try:
    credentials = get_credentials()
except Exception as e:
    print(f"⚠️  Warning: Could not initialize Google APIs: {e}")
    credentials = None

# googleapiclient (httplib2 transport) is NOT thread-safe when one service
# object is shared across threads. Route handlers are plain `def` now (they
# run in FastAPI's threadpool for real concurrency), so each thread builds
# its own service lazily and reuses it. Discovery docs are cached in-process
# so per-thread build() costs no extra network round-trips.
class _DiscoveryCache:
    _docs = {}
    def get(self, url):
        return self._docs.get(url)
    def set(self, url, content):
        self._docs[url] = content

_thread_services = threading.local()

def _thread_service(kind, name, version):
    if credentials is None:
        return None
    svc = getattr(_thread_services, kind, None)
    if svc is None:
        svc = build(name, version, credentials=credentials, cache=_DiscoveryCache())
        setattr(_thread_services, kind, svc)
    return svc

def get_sheets_service():
    return _thread_service('sheets', 'sheets', 'v4')

def get_drive_service():
    return _thread_service('drive', 'drive', 'v3')

# ============================================================================
# DATA MODELS
# ============================================================================

class Question(BaseModel):
    uid: str
    qno: str
    subtopic: str
    difficulty: str
    level: Optional[str] = None  # Stream/Subject level
    subject: str = "Physics"  # Physics, Math, etc. (from optional 'Subject' sheet column)
    question_text: str
    options: str
    answer: str
    explanation: Optional[str] = None  # 'Explanation' column from the sheet -- shown after the user submits an answer
    image_url: Optional[str] = None
    setup_image_url: Optional[str] = None  # Setup diagram URL (for frontend to display)
    diagram_file_id: Optional[str] = None  # File ID from Diagram column (setup)
    options_image_uid: Optional[str] = None  # File UID from IMAGE: in Options column
    option_type: str = "TEXT"  # TEXT, TABLE, or IMAGE
    table_headers: Optional[List] = None  # For TABLE type - can be List[str] or List[List[str]] for multi-level
    table_header_levels: Optional[int] = None  # Number of header levels (1 for simple, 2+ for nested)
    table_header_colspan: Optional[List[List[int]]] = None  # Colspan for each header cell (for multi-level headers)
    table_rows: Optional[List[dict]] = None  # For TABLE type

class QuizRequest(BaseModel):
    subject: Optional[str] = None  # Physics, Math, ...
    difficulty: Optional[str] = None
    subtopic: Optional[str] = None  # Backwards-compat single subtopic
    subtopics: Optional[List[str]] = None  # Up to 3 subtopics; questions distributed across them
    level: Optional[str] = None  # Stream/Subject level
    count: int = 5

class QuizResponse(BaseModel):
    questions: List[Question]
    count: int
    filters: dict

# ============================================================================
# AUTHENTICATION DATA MODELS
# ============================================================================

class SignupRequest(BaseModel):
    email: str
    password: str
    name: str
    school: Optional[str] = None
    student_class: Optional[str] = None
    teacher: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str

class CompleteProfileRequest(BaseModel):
    school: str
    student_class: str
    teacher: str

class GoogleLoginRequest(BaseModel):
    token: str  # Google ID token from frontend

class AuthResponse(BaseModel):
    success: bool
    message: str
    token: Optional[str] = None
    user: Optional[dict] = None

class UserProfile(BaseModel):
    id: int
    email: str
    name: str
    created_at: str

# ============================================================================
# QUIZ HISTORY DATA MODELS
# ============================================================================

class QuizSubmissionRequest(BaseModel):
    """Submit a completed quiz for scoring and history"""
    difficulty: Optional[str] = None
    subtopic: Optional[str] = None
    level: Optional[str] = None
    count: int
    time_spent_seconds: int  # How long to complete the quiz
    user_answers: Dict[int, str]  # {question_index: "answer"}
    score: Optional[int] = None  # Score calculated by frontend (trusted)
    percentage: Optional[int] = None  # Percentage calculated by frontend (trusted)
    questions: Optional[List[Dict]] = None  # Full questions for verification and storage
    parent_attempt_id: Optional[int] = None  # Set when this is a retake of a saved quiz
    name: Optional[str] = None  # Quiz name; used for first attempt only (retakes inherit from parent)
    quiz_type: Optional[str] = 'practice'  # 'daily' awards XP/gems/streak; 'practice' is reward-free

class QuizAttempt(BaseModel):
    """A single quiz attempt record"""
    id: int
    user_id: int
    difficulty: Optional[str]
    subtopic: Optional[str]
    score: int  # Number correct
    percentage: int  # Score percentage
    total_questions: int
    time_spent_seconds: int
    attempted_at: str
    questions_data: Optional[dict] = None  # JSON of questions and answers for review

class QuizHistoryResponse(BaseModel):
    """History list response"""
    attempts: List[QuizAttempt]
    total_attempts: int
    average_score: float

# ============================================================================
# OPTION TYPE PARSING
# ============================================================================

def calculate_colspan(header_row: List[str]) -> List[int]:
    """
    Calculate colspan for each header cell by detecting consecutive duplicates.
    Example: ["iron", "iron", "steel", "steel"] → [2, 2]
    (iron spans 2 columns, steel spans 2 columns)
    """
    if not header_row:
        return []

    colspan_list = []
    i = 0
    while i < len(header_row):
        current_label = header_row[i]
        colspan = 1

        # Count how many times this label repeats consecutively
        while i + colspan < len(header_row) and header_row[i + colspan] == current_label:
            colspan += 1

        colspan_list.append(colspan)
        i += colspan

    return colspan_list

def parse_option_type(options_str: str) -> Tuple[str, str, Optional[List], Optional[List[dict]], Optional[str], Optional[int], Optional[List[List[int]]]]:
    """
    Parse options string to detect type and structure
    Returns: (option_type, parsed_options, table_headers, table_rows, image_uid, header_levels, header_colspan)

    TEXT format (default):
    A) Option A text
    B) Option B text
    C) Option C text
    D) Option D text

    TABLE format (single-level headers):
    TABLE:
    Header1 | Header2
    A) RowVal1 | RowVal2
    B) RowVal3 | RowVal4
    ...

    TABLE format (multi-level headers):
    TABLE:
    TopHeader1 | TopHeader1 | TopHeader2 | TopHeader2
    SubHeader1 | SubHeader2 | SubHeader3 | SubHeader4
    A) RowVal1 | RowVal2 | RowVal3 | RowVal4
    B) ...

    IMAGE format:
    IMAGE:uid
    (All options shown in a single diagram image with file uid)
    """
    options_str = options_str.strip()
    image_uid = None

    if options_str.startswith('IMAGE:'):
        # Extract UID from IMAGE:uid format
        if ':' in options_str:
            image_uid = options_str.split(':', 1)[1].strip()
        return ('IMAGE', options_str, None, None, image_uid, None, None)

    if options_str.startswith('TABLE:'):
        # Parse table format
        lines = options_str.split('\n')
        header_rows = []
        table_rows = []
        data_started = False

        for i, line in enumerate(lines):
            line = line.strip()
            # Strip a leading "TABLE:" marker. The first header row may be
            # written on the SAME line as TABLE: (e.g. "TABLE: H1 | H2") —
            # keep that text instead of discarding the whole line.
            if line.startswith('TABLE:'):
                line = line[6:].strip()
            if not line:
                continue

            # Check if this is a data row (starts with A, B, C, or D followed by ))
            is_data_row = line and line[0] in 'ABCD' and len(line) > 1 and line[1] == ')'

            if is_data_row:
                data_started = True

            if not data_started:
                # This is a header row. Drop empty cells produced by
                # surrounding pipes (| a | b |) and the leading blank
                # "option-letter" column, so flat_headers lines up 1:1 with
                # each data row's values.
                header_parts = [h.strip() for h in line.split('|')]
                header_parts = [h for h in header_parts if h]
                if header_parts:
                    header_rows.append(header_parts)
            else:
                # Parse data row
                if is_data_row:
                    row_data = {}
                    parts = [p.strip() for p in line.split('|')]

                    # First part has the letter: "A) value" or "A)"
                    first_part = parts[0]
                    letter = first_part[0]
                    # Remove letter and ")" and any spaces: "A) value" -> "value"
                    value = first_part[1:].lstrip(') ').strip() if len(first_part) > 1 else ''

                    # Two data-row layouts occur in the sheet:
                    #   "A) v1 | v2"   -> the letter is glued to the first value
                    #   "A) | v1 | v2" -> the letter sits in its own pipe cell,
                    #                     so the value glued to the letter is ''
                    # Build one positional cell list that works for both, so the
                    # values line up 1:1 with the (letter-less) flat headers.
                    if value:
                        cells = [value] + parts[1:]
                    else:
                        cells = parts[1:]
                    # A closing pipe ("...| v2 |") leaves one trailing blank cell.
                    if line.endswith('|') and cells and cells[-1] == '':
                        cells.pop()

                    # Get flat headers for mapping (use last header row for simple cases)
                    flat_headers = header_rows[-1] if header_rows else []

                    # Map cell values to headers positionally
                    for j, header in enumerate(flat_headers):
                        if j < len(cells):
                            row_data[header] = cells[j]

                    row_data['_letter'] = letter
                    # Positional cell values (letter prefix stripped) so the
                    # frontend can render the row even when the table has no
                    # header row to key the values by.
                    row_data['_cells'] = cells
                    table_rows.append(row_data)

        # Determine header format
        headers = header_rows if header_rows else None
        header_levels = len(header_rows) if header_rows else None
        header_colspan = None

        # Calculate colspan for multi-level headers
        if header_levels and header_levels > 1:
            # Calculate colspan for the first header level only
            # The first header has colspan (e.g., "iron" spans 2 columns)
            # Other levels span 1 column each
            first_level_colspan = calculate_colspan(header_rows[0]) if header_rows else []
            header_colspan = [first_level_colspan]  # Only track colspan for first level
            # Other levels have no colspan (each cell is 1 column)
            for i in range(1, header_levels):
                header_colspan.append(None)  # None means no colspan for this level

        # If single header level, return as flat list for backwards compatibility
        if header_levels == 1:
            headers = header_rows[0] if header_rows else None
            header_levels = None
            header_colspan = None

        return ('TABLE', options_str, headers, table_rows, None, header_levels, header_colspan)

    # Default to TEXT
    return ('TEXT', options_str, None, None, None, None, None)

# ============================================================================
# DATABASE HELPER FUNCTIONS
# ============================================================================

# ---------------------------------------------------------------------------
# Connection pool — opening a fresh MySQL connection per request is expensive
# (TCP + auth handshake, tens to hundreds of ms each, especially to a remote
# DB). We keep a small pool of live connections and reuse them. Returning a
# connection is done by calling .close() on it, exactly like before — the
# _PooledConn wrapper intercepts that and hands the real connection back to
# the pool instead of tearing it down. No call site needs to change.
# ---------------------------------------------------------------------------
_DB_POOL = queue.Queue(maxsize=8)


class _PooledConn:
    """Thin proxy around a pymysql connection. .close() returns it to the
    pool; every other attribute/method delegates to the real connection."""

    def __init__(self, raw):
        self._raw = raw
        self._returned = False

    def __getattr__(self, name):
        # Only reached for attributes not defined on the proxy itself.
        return getattr(self._raw, name)

    def cursor(self, *args, **kwargs):
        return self._raw.cursor(*args, **kwargs)

    def commit(self):
        return self._raw.commit()

    def rollback(self):
        return self._raw.rollback()

    def close(self):
        # Return the underlying connection to the pool rather than closing it.
        if self._returned:
            return
        self._returned = True
        try:
            self._raw.rollback()        # clear any half-finished transaction
            _DB_POOL.put_nowait(self._raw)
        except Exception:
            # Pool full, or the connection is dead — just drop it for real.
            try:
                self._raw.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def get_db_connection():
    """Get a MySQL connection from the pool (or open a new one if the pool is
    empty). Call .close() on the returned object to release it back."""
    raw = None
    try:
        raw = _DB_POOL.get_nowait()
    except queue.Empty:
        raw = None

    if raw is not None:
        # Make sure the pooled connection is still alive; reconnect if not.
        try:
            raw.ping(reconnect=True)
        except Exception:
            try:
                raw.close()
            except Exception:
                pass
            raw = None

    if raw is None:
        try:
            raw = pymysql.connect(**DB_CONFIG)
        except Exception as e:
            print(f"❌ Database connection error: {e}")
            raise HTTPException(status_code=500, detail="Database connection failed")

    return _PooledConn(raw)

def init_database():
    """Initialize database tables if they don't exist"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255),
                google_id VARCHAR(255),
                name VARCHAR(255),
                avatar_url LONGTEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Backfill avatar_url on pre-existing databases.
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'users'
              AND COLUMN_NAME = 'avatar_url'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE users ADD COLUMN avatar_url LONGTEXT NULL AFTER name")
            print("🔧 Added avatar_url column to users")

        # equipped (JSON) — wearables per slot.
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'users'
              AND COLUMN_NAME = 'equipped'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE users ADD COLUMN equipped JSON NULL AFTER avatar_url")
            print("🔧 Added equipped column to users")

        # School / class / teacher — collected on the signup form.
        for _col, _ddl in (
            ('school',        "ALTER TABLE users ADD COLUMN school VARCHAR(255) NULL"),
            ('student_class', "ALTER TABLE users ADD COLUMN student_class VARCHAR(255) NULL"),
            ('teacher',       "ALTER TABLE users ADD COLUMN teacher VARCHAR(255) NULL"),
        ):
            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'users' AND COLUMN_NAME = %s
            """, (_col,))
            if cursor.fetchone()[0] == 0:
                cursor.execute(_ddl)
                print(f"🔧 Added {_col} column to users")

        # XP column (Phase 3 leaderboard). Backfill in the same conditional so
        # we only compute legacy XP exactly once per database.
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'users'
              AND COLUMN_NAME = 'xp'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE users ADD COLUMN xp BIGINT NOT NULL DEFAULT 0 AFTER avatar_url")
            print("🔧 Added xp column to users")

            # Backfill XP from quiz_attempts. Streak multiplier is omitted —
            # we don't know what the streak was at the moment of each attempt,
            # so legacy XP is base * difficulty only. Live submissions get the
            # full formula including the streak bonus.
            try:
                cursor.execute("""
                    SELECT user_id,
                           SUM(score * %s *
                               CASE LOWER(COALESCE(difficulty, ''))
                                   WHEN 'easy'   THEN %s
                                   WHEN 'medium' THEN %s
                                   WHEN 'hard'   THEN %s
                                   ELSE 1.0
                               END) AS xp
                    FROM quiz_attempts
                    WHERE score IS NOT NULL
                    GROUP BY user_id
                """, (XP_BASE_PER_CORRECT, XP_DIFFICULTY_MULT['easy'],
                      XP_DIFFICULTY_MULT['medium'], XP_DIFFICULTY_MULT['hard']))
                totals = cursor.fetchall()
                for uid, total in totals:
                    cursor.execute(
                        "UPDATE users SET xp = %s WHERE id = %s",
                        (int(round(total or 0)), uid),
                    )
                conn.commit()
                print(f"🔧 Backfilled XP for {len(totals)} user(s) from quiz_attempts")
            except Exception as _e:
                print(f"⚠️ XP backfill failed (non-fatal): {_e}")

        # Gems column (StarQuest §05). Spendable currency, separate from XP.
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'users'
              AND COLUMN_NAME = 'gems'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE users ADD COLUMN gems BIGINT NOT NULL DEFAULT 0 AFTER xp")
            print("🔧 Added gems column to users")

        # Per-user daily_goal (StarQuest §06). 10 / 15 / 20 cumulative correct per day.
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'users'
              AND COLUMN_NAME = 'daily_goal'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE users ADD COLUMN daily_goal SMALLINT NOT NULL DEFAULT 10 AFTER gems")
            print("🔧 Added daily_goal column to users")

        # Dev-tools simulated clock — day offset for the streak test panel.
        # Always 0 in production; the test panel bumps it to fast-forward days.
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'users'
              AND COLUMN_NAME = 'test_day_offset'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE users ADD COLUMN test_day_offset INT NOT NULL DEFAULT 0 AFTER daily_goal")
            print("🔧 Added test_day_offset column to users")

        # is_teacher (Teacher Dashboard) — flipped manually in the DB to grant
        # an account the teacher-dashboard view. There is no API to set this.
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'users'
              AND COLUMN_NAME = 'is_teacher'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE users ADD COLUMN is_teacher BOOLEAN NOT NULL DEFAULT FALSE AFTER test_day_offset")
            print("🔧 Added is_teacher column to users")

        # user_rewards (StarQuest §05 — rewards shop). One row per redemption.
        # UNIQUE(user_id, reward_id) enforces once-per-item from the catalogue.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_rewards (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                reward_id VARCHAR(64) NOT NULL,
                cost INT NOT NULL,
                redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fulfilled_at TIMESTAMP NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE KEY uniq_user_reward (user_id, reward_id),
                INDEX idx_user_rewards_user (user_id)
            )
        """)

        # Create quiz_attempts table for storing quiz history
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS quiz_attempts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(255) NULL,
                difficulty VARCHAR(50),
                subtopic VARCHAR(255),
                score INT,
                percentage INT,
                total_questions INT,
                time_spent_seconds INT,
                questions_data LONGTEXT,
                parent_attempt_id INT NULL,
                quiz_type VARCHAR(20) DEFAULT 'practice',
                attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                INDEX idx_user_id (user_id),
                INDEX idx_attempted_at (attempted_at),
                INDEX idx_parent_attempt_id (parent_attempt_id)
            )
        """)

        # Backfill the `name` column on pre-existing databases.
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'quiz_attempts'
              AND COLUMN_NAME = 'name'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                ALTER TABLE quiz_attempts
                ADD COLUMN name VARCHAR(255) NULL AFTER user_id
            """)
            print("🔧 Added name column to quiz_attempts")

        # Backfill the parent_attempt_id column on pre-existing databases that were
        # created before this column existed. Safe to run every startup.
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'quiz_attempts'
              AND COLUMN_NAME = 'parent_attempt_id'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                ALTER TABLE quiz_attempts
                ADD COLUMN parent_attempt_id INT NULL AFTER questions_data,
                ADD INDEX idx_parent_attempt_id (parent_attempt_id)
            """)
            print("🔧 Added parent_attempt_id column to quiz_attempts")

        # Backfill the quiz_type column (practice | placement | ranked | daily).
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'quiz_attempts'
              AND COLUMN_NAME = 'quiz_type'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                ALTER TABLE quiz_attempts
                ADD COLUMN quiz_type VARCHAR(20) DEFAULT 'practice'
            """)
            print("🔧 Added quiz_type column to quiz_attempts")

        # Create streaks table (Phase 2 — one row per user, global daily streak)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS streaks (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                current_streak INT DEFAULT 0,
                longest_streak INT DEFAULT 0,
                last_qualified_date DATE NULL,
                freezes_available INT DEFAULT 1,
                freeze_last_granted DATE NULL,
                freeze_used_date DATE NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE KEY uniq_streak_user (user_id)
            )
        """)

        # Backfill freeze_used_date on pre-existing databases
        cursor.execute("""
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'streaks'
              AND COLUMN_NAME = 'freeze_used_date'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE streaks ADD COLUMN freeze_used_date DATE NULL AFTER freeze_last_granted")
            print("\U0001f527 Added freeze_used_date column to streaks")

        # Create rank_history table (Phase 2 — append a row on every rank change)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rank_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                subject VARCHAR(100) NOT NULL,
                rank_band VARCHAR(4) NOT NULL,
                rank_score INT NOT NULL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                INDEX idx_rh_user_subject (user_id, subject)
            )
        """)

        # Create daily_challenges table (Phase 2 — one row per user+subject+day)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_challenges (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                subject VARCHAR(100) NOT NULL,
                challenge_date DATE NOT NULL,
                score INT NOT NULL,
                total INT NOT NULL,
                percentage INT NOT NULL,
                passed BOOLEAN DEFAULT FALSE,
                attempts INT DEFAULT 1,
                xp BIGINT NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE KEY uniq_dc_user_subject_date (user_id, subject, challenge_date),
                INDEX idx_dc_user_subject (user_id, subject)
            )
        """)

        # Per-day XP earned — drives the daily / weekly leaderboards.
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'daily_challenges'
              AND COLUMN_NAME = 'xp'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE daily_challenges ADD COLUMN xp BIGINT NOT NULL DEFAULT 0 AFTER attempts")
            print("🔧 Added xp column to daily_challenges")

        # Create user_subject_ranks table (Phase 1 ranking system)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_subject_ranks (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                subject VARCHAR(100) NOT NULL,
                rank_band VARCHAR(4) NOT NULL,
                rank_score INT NOT NULL,
                placed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE KEY uniq_user_subject (user_id, subject),
                INDEX idx_usr_rank (user_id)
            )
        """)

        conn.commit()
        print("✅ Database tables initialized")
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"⚠️  Warning: Could not initialize database tables: {e}")

def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)

# Password every teacher-initiated reset falls back to. Students sign in with
# this and are expected to change it in Settings afterwards.
DEFAULT_RESET_PASSWORD = "Curious"

def create_jwt_token(user_id: int, email: str, is_teacher: bool = False) -> str:
    """Create a JWT token for the user. `is_teacher` is baked into the claim
    so the frontend can route a teacher straight into the teacher dashboard
    without a second round-trip."""
    payload = {
        'user_id': user_id,
        'email': email,
        'is_teacher': bool(is_teacher),
        'exp': datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_jwt_token(token: str) -> Optional[dict]:
    """Verify and decode a JWT token"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def require_teacher(authorization: Optional[str]) -> dict:
    """Validate a bearer token and assert the user has the is_teacher claim.
    Raises 401 if no/invalid token, 403 if not a teacher. Returns the payload."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No authorization token")
    payload = verify_jwt_token(authorization.replace("Bearer ", ""))
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if not payload.get('is_teacher'):
        raise HTTPException(status_code=403, detail="Teacher access required")
    return payload

# ============================================================================
# QUESTION CACHING
# ============================================================================

class QuestionCache:
    def __init__(self):
        self.questions = []
        self.is_loaded = False
        self.image_url_cache = {}
        self.setup_info_map = {}  # Maps question UID to {'text': ..., 'file_id': ...}
        self.file_map = {}  # Pre-loaded file mapping by name (for faster lookup)
        # Serializes load_questions/load_file_map — with threadpool handlers
        # two threads could otherwise parse/scan simultaneously.
        self._load_lock = threading.Lock()
        # ── Staleness tracking (auto-refresh) ──
        # loaded_at: when the bank was last (re)parsed or last confirmed fresh.
        # sheet_fingerprint: Drive modifiedTime of the source workbook(s) at
        # load time — lets revalidation skip the expensive re-parse when the
        # Sheet hasn't actually changed.
        self.loaded_at = 0.0
        self.sheet_fingerprint = None
        self._revalidate_gate = threading.Lock()
        self._revalidating = False

    def load_file_map(self, force=False):
        """Pre-load every image under QUESTION_FOLDER_IDS into memory for lookup.

        Walks each root folder RECURSIVELY (Google Drive is a tree of folders),
        so images resolve whether they live directly in a folder (old flat
        layout) OR inside per-paper subfolders (new layout). Files are still
        keyed by name, so the Sheet keeps referencing them by filename/UID
        exactly as before — nothing about the question rows has to change.

        Drive's files.list() returns at most pageSize results per call and
        gives a nextPageToken if there are more; we page until it's gone.

        Pass force=True to re-scan even if file_map is already populated
        (useful after uploading new images while the server is running).
        """
        with self._load_lock:
            return self._load_file_map_unlocked(force)

    def _load_file_map_unlocked(self, force=False):
        if not get_drive_service():
            return
        if self.file_map and not force:
            return  # Already loaded

        if force:
            self.file_map = {}

        FOLDER_MIME = 'application/vnd.google-apps.folder'
        try:
            print(f"📁 Pre-loading file map from Google Drive (recursive) — roots: {QUESTION_FOLDER_IDS}")
            files = []
            seen_folders = set()
            # Walk each root independently so a permission error on one drive
            # (e.g. not shared with the service account) doesn't wipe the whole
            # map. Per-root counts are logged so you can see what each scanned.
            for root in QUESTION_FOLDER_IDS:
                root_count = 0
                queue = [root]
                while queue:
                    folder_id = queue.pop(0)
                    if not folder_id or folder_id in seen_folders:
                        continue
                    seen_folders.add(folder_id)
                    page_token = None
                    try:
                        while True:
                            results = get_drive_service().files().list(
                                q=f"'{folder_id}' in parents and trashed=false",
                                spaces='drive',
                                fields='nextPageToken, files(id, name, mimeType)',
                                pageSize=1000,
                                pageToken=page_token,
                                includeItemsFromAllDrives=True,
                                supportsAllDrives=True,
                            ).execute()
                            for f in results.get('files', []):
                                if f.get('mimeType') == FOLDER_MIME:
                                    queue.append(f['id'])    # descend into subfolder
                                else:
                                    files.append(f)
                                    root_count += 1
                            page_token = results.get('nextPageToken')
                            if not page_token:
                                break
                    except Exception as fe:
                        print(f"   ⚠️  Could not list folder {folder_id} (root {root}): {fe}")
                        continue
                print(f"   • root {root}: {root_count} image files")

            # Map name → file ID. setdefault = first occurrence wins, so a file
            # found in a shallower folder isn't clobbered by a same-named file
            # deeper down. Keep image filenames unique across papers.
            for f in files:
                name = f['name']
                file_id = f['id']
                self.file_map.setdefault(name, file_id)
                self.file_map.setdefault(name.lower(), file_id)
                if '.' in name:
                    name_no_ext = name.rsplit('.', 1)[0]
                    self.file_map.setdefault(name_no_ext, file_id)
                    self.file_map.setdefault(name_no_ext.lower(), file_id)

            print(f"✅ Loaded {len(files)} image files from {len(seen_folders)} folder(s) for fast lookup")
            return

        except Exception as e:
            print(f"⚠️  Warning: Could not pre-load files: {e}")
            self.file_map = {}

    def _extract_file_id(self, diagram_cell: str) -> Optional[str]:
        """
        Extract file ID from various formats:
        - IMAGE:file_id: "IMAGE:PHY-ACSBR2019-P1-4E5N-002-setup"
        - IMAGE:(file_id): "IMAGE:(1xyz...)"
        - Pure file ID: "1xyz..."
        - Google Drive URL: "https://drive.google.com/file/d/1xyz.../view"
        - Shareable link: "https://drive.google.com/open?id=1xyz..."
        - Filename: Will be looked up in file_map later
        """
        if not diagram_cell:
            return None

        diagram_cell = diagram_cell.strip()

        # Handle IMAGE:file_id format (with or without parentheses)
        if diagram_cell.lower().startswith('image:'):
            # Remove "IMAGE:" prefix
            file_id = diagram_cell[6:].strip()  # Skip "IMAGE:"

            # Remove parentheses if present: (file_id) → file_id
            if file_id.startswith('(') and file_id.endswith(')'):
                file_id = file_id[1:-1].strip()

            return file_id if file_id else None

        # Extract from Google Drive URL: /d/FILE_ID/
        if '/d/' in diagram_cell:
            parts = diagram_cell.split('/d/')
            if len(parts) > 1:
                file_id = parts[1].split('/')[0]
                return file_id.strip()

        # Extract from query parameter: id=FILE_ID
        if 'id=' in diagram_cell:
            parts = diagram_cell.split('id=')
            if len(parts) > 1:
                file_id = parts[1].split('&')[0]
                return file_id.strip()

        # Return as-is (could be filename or file ID)
        # Will be resolved to actual file ID via file_map lookup later
        return diagram_cell

    def load_questions(self):
        """Load all questions from Google Sheet and cache them"""
        with self._load_lock:
            if self.is_loaded:
                return
            return self._load_questions_unlocked()

    # ── Auto-refresh (stale-while-revalidate) ────────────────────────────
    #
    # The bank used to be loaded ONCE at startup and then served forever, so
    # rows deleted from the Sheet (or images re-uploaded under new Drive IDs)
    # kept appearing until the server was restarted. ensure_fresh() fixes
    # that: once the TTL expires, the NEXT request kicks off a background
    # thread that asks Drive for the workbook's modifiedTime (one cheap API
    # call) and re-parses only if the Sheet actually changed. Requests are
    # never blocked — they serve the current bank while the check/reload
    # runs ("stale-while-revalidate"). Grading is unaffected by mid-quiz
    # reloads because /api/quiz/submit grades the questions echoed back by
    # the frontend, not a fresh cache lookup.

    def _get_sheet_fingerprint(self):
        """Drive modifiedTime of the source workbook(s), or None if the
        Drive API is unavailable / the file isn't visible to the service
        account. None disables the 'skip reload if unchanged' shortcut —
        revalidation then reloads on every TTL expiry (safe, just costlier)."""
        drive = get_drive_service()
        if not drive:
            return None
        stamps = []
        for sid in (SPREADSHEET_ID, P6_MATH_SPREADSHEET_ID):
            if not sid:
                continue
            try:
                meta = drive.files().get(
                    fileId=sid, fields='modifiedTime', supportsAllDrives=True,
                ).execute()
                stamps.append(meta.get('modifiedTime'))
            except Exception as e:
                print(f"⚠️  Could not read modifiedTime for {sid}: {e}")
                return None
        return tuple(stamps) if stamps else None

    def ensure_fresh(self):
        """Cheap staleness gate — call at the top of question-serving paths.

        First load is synchronous (nothing to serve yet). After that, when
        the TTL has expired, spawn ONE background revalidation thread and
        return immediately with the current bank."""
        if not self.is_loaded:
            self.load_questions()
            return
        if QUESTIONS_CACHE_TTL_SECONDS <= 0:
            return  # auto-refresh disabled
        if time.time() - self.loaded_at < QUESTIONS_CACHE_TTL_SECONDS:
            return
        with self._revalidate_gate:
            if self._revalidating:
                return  # a check is already in flight
            self._revalidating = True
        threading.Thread(target=self._background_revalidate,
                         daemon=True, name="sheet-revalidate").start()

    def _background_revalidate(self):
        try:
            current = self._get_sheet_fingerprint()
            if current is not None and current == self.sheet_fingerprint:
                # Sheet untouched — just extend the TTL, skip the re-parse.
                self.loaded_at = time.time()
                return
            print("🔄 Sheet changed (or fingerprint unavailable) — reloading question bank...")
            self.refresh()
        except Exception as e:
            # Don't hammer the API on persistent errors; try again next TTL.
            print(f"⚠️  Background revalidation failed: {e}")
            self.loaded_at = time.time()
        finally:
            with self._revalidate_gate:
                self._revalidating = False

    def refresh(self, rescan_files=True):
        """Force a full reload of the question bank from the Sheet.

        Also clears the per-UID image URL cache and (by default) rescans the
        Drive file map in a background thread, so images re-uploaded under a
        NEW Drive file ID resolve again instead of 404ing on the stale ID."""
        with self._load_lock:
            self._load_questions_unlocked()
        self.image_url_cache = {}
        if rescan_files:
            threading.Thread(target=self.load_file_map, kwargs={'force': True},
                             daemon=True, name="drive-rescan").start()
        return len(self.questions)

    def _load_questions_unlocked(self):
        # Parse into LOCAL lists and swap into place only at the very end.
        # Two reasons: (1) a failed attempt can never leave partial rows for a
        # retry to append onto (duplicated question bank), and (2) background
        # auto-refresh reloads happen while request threads are reading
        # self.questions — they must keep seeing the complete old bank until
        # the new one is fully parsed (atomic reference swap).
        new_questions = []
        new_setup_info = {}

        if not get_sheets_service():
            raise RuntimeError("Google Sheets API not initialized")

        # Snapshot the workbook fingerprint BEFORE reading rows — if the Sheet
        # is edited mid-parse, the next revalidation sees a changed fingerprint
        # and reloads again (never wrongly concludes "already up to date").
        fingerprint = self._get_sheet_fingerprint()

        try:
            # One batchGet fetches every tab in a single API call. BUT if any
            # named tab is missing (deleted/renamed in the workbook), Sheets
            # rejects the ENTIRE batch with 400 "Unable to parse range: <tab>"
            # and the app can never load questions (every quiz endpoint 500s).
            # So on batch failure, fall back to per-tab fetches and just skip
            # tabs that don't exist.
            svc = get_sheets_service()
            fetched = []  # [(tab_name, valueRange), ...]
            try:
                batch = svc.spreadsheets().values().batchGet(
                    spreadsheetId=SPREADSHEET_ID,
                    ranges=SHEET_NAMES,
                ).execute()
                fetched = list(zip(SHEET_NAMES, batch.get('valueRanges', [])))
            except Exception as batch_err:
                print(f"⚠️  batchGet failed ({batch_err}); retrying tabs individually")
                for tab in SHEET_NAMES:
                    try:
                        vr = svc.spreadsheets().values().get(
                            spreadsheetId=SPREADSHEET_ID,
                            range=tab,
                        ).execute()
                        fetched.append((tab, vr))
                    except Exception as tab_err:
                        print(f"⚠️  Skipping missing tab '{tab}': {tab_err}")

            # ── P6 Math: separate workbook, appended as one more source.
            # Failure here must never take down the physics bank.
            if P6_MATH_SPREADSHEET_ID:
                try:
                    vr = svc.spreadsheets().values().get(
                        spreadsheetId=P6_MATH_SPREADSHEET_ID,
                        range='A:ZZ',   # no tab name -> first sheet
                    ).execute()
                    if vr.get('values'):
                        fetched.append((P6_MATH_TAB_LABEL, vr))
                except Exception as p6_err:
                    print(f"⚠️  Skipping P6 Math workbook: {p6_err}")

            # Merge all tabs into one rows list. The header row from the FIRST
            # non-empty tab wins; data rows from every tab are appended (their
            # own header rows are skipped).
            rows = []
            headers = None
            per_tab_counts = []
            for tab_name, vr in fetched:
                tab_rows = vr.get('values', [])
                if not tab_rows:
                    per_tab_counts.append(f"{tab_name}: 0")
                    continue
                tab_header = tab_rows[0]
                tab_data = tab_rows[1:]
                if headers is None:
                    headers = tab_header
                    rows.append(tab_header)
                elif (tab_header != headers
                      and all(h in tab_header for h in ('UID', 'Question', 'Options', 'Answer'))):
                    # Same column NAMES but different positions/extras (e.g. the
                    # external P6 workbook): remap each row to the first tab's
                    # layout by header name instead of trusting positions.
                    idx = {h: i for i, h in enumerate(tab_header)}
                    tab_data = [
                        [(r[idx[h]] if h in idx and idx[h] < len(r) else '') for h in headers]
                        for r in tab_data
                    ]
                if tab_name == P6_MATH_TAB_LABEL and 'Level' in headers:
                    # Force Level on every P6 row — filtering must not depend
                    # on how rows happen to be tagged in that workbook.
                    li = headers.index('Level')
                    fixed = []
                    for r in tab_data:
                        r = list(r)
                        while len(r) <= li:
                            r.append('')
                        r[li] = P6_MATH_LEVEL
                        fixed.append(r)
                    tab_data = fixed
                # Append data rows. We deliberately do NOT enforce header
                # equality across tabs; the column-index map below is built
                # off the first tab's headers and the per-row width handling
                # already pads short rows with empty strings.
                rows.extend(tab_data)
                per_tab_counts.append(f"{tab_name}: {len(tab_data)} rows")

            if not rows:
                print(f"⚠️  No questions found across tabs: {SHEET_NAMES}")
                return

            print(f"📚 Loaded tabs — {' · '.join(per_tab_counts)}")
            print(f"📋 Headers: {headers}")

            # Create column index map
            col_map = {header: idx for idx, header in enumerate(headers)}

            # Use 'Topic' if available, fallback to 'Subtopic' for backward compatibility
            topic_col = 'Topic' if 'Topic' in col_map else 'Subtopic'
            # 'Explanation' column (optional, case-insensitive): why an answer is right/wrong
            expl_col_idx = next((idx for hdr, idx in col_map.items()
                                 if str(hdr).strip().lower() == 'explanation'), None)
            required_cols = ['UID', 'QNo', 'Difficulty', 'Question', 'Options', 'Answer']

            # Verify all required columns exist
            missing = [col for col in required_cols if col not in col_map]
            if missing:
                raise ValueError(f"Missing columns in sheet: {missing}")

            print(f"Available columns: {list(col_map.keys())}")
            print(f"📌 Using '{topic_col}' column for topic grouping")

            # Parse questions (skip setup rows, but map their diagrams)
            for row_idx, row in enumerate(rows[1:], start=2):
                try:
                    # Handle rows that might be shorter
                    while len(row) < len(headers):
                        row.append('')

                    uid = row[col_map['UID']].strip()
                    qno = row[col_map['QNo']].strip()
                    subtopic = row[col_map[topic_col]].strip()
                    difficulty = row[col_map['Difficulty']].strip()
                    level = row[col_map['Level']].strip() if 'Level' in col_map else None
                    subject = row[col_map['Subject']].strip() if 'Subject' in col_map else 'Physics'
                    question_text = row[col_map['Question']].strip()
                    options = row[col_map['Options']].strip()
                    answer = row[col_map['Answer']].strip()
                    explanation = (row[expl_col_idx].strip()
                                   if expl_col_idx is not None and expl_col_idx < len(row)
                                   else None) or None

                    # Get diagram file ID if Diagram column exists
                    diagram_file_id = None
                    if 'Diagram' in col_map:
                        diagram_cell = row[col_map['Diagram']].strip()
                        if diagram_cell and diagram_cell.lower() != 'na':
                            # Extract file ID from various formats
                            diagram_file_id = self._extract_file_id(diagram_cell)

                    # Skip empty UIDs
                    if not uid:
                        continue

                    # Handle setup rows: store their text and diagram
                    is_setup = uid.endswith('-setup') or subtopic.lower() == 'question setup'
                    if is_setup:
                        print(f"  🔵 SETUP ROW (Row {row_idx}): uid='{uid}'")
                        print(f"       text='{question_text[:60]}...'")
                        print(f"       diagram_file_id='{diagram_file_id}'")
                        # Extract main question UID (remove -setup suffix)
                        main_uid = uid.replace('-setup', '')
                        # The setup diagram normally comes from the Diagram column.
                        # Some setup rows instead declare it in the Options column
                        # as "IMAGE:<uid>" -- fall back to that so the diagram is
                        # not silently dropped.
                        setup_file_id = diagram_file_id
                        if not setup_file_id and options.upper().startswith('IMAGE:'):
                            setup_file_id = options.split(':', 1)[1].strip()
                            print(f"       diagram from Options IMAGE ref: {setup_file_id}")
                        new_setup_info[main_uid] = {
                            'text': question_text,
                            'file_id': setup_file_id  # Can be None
                        }
                        print(f"       ✅ MAPPED: {main_uid}")
                        continue  # Skip adding setup row as a question

                    # Skip rows without question text
                    if not question_text:
                        continue

                    # Parse option type
                    option_type, parsed_options, table_headers, table_rows, options_image_uid, header_levels, header_colspan = parse_option_type(options)

                    question = Question(
                        uid=uid,
                        qno=qno,
                        subtopic=canonical_topic(subtopic, combined=_is_nonpure(level), level_key=_level_key(level)),
                        difficulty=difficulty,
                        level=level,
                        subject=subject or 'Physics',
                        question_text=question_text,
                        options=parsed_options,
                        answer=answer,
                        option_type=option_type,
                        table_headers=table_headers,
                        table_header_levels=header_levels,
                        table_header_colspan=header_colspan,
                        table_rows=table_rows,
                        diagram_file_id=diagram_file_id,
                        options_image_uid=options_image_uid,
                        explanation=explanation
                    )
                    new_questions.append(question)

                except Exception as e:
                    print(f"⚠️  Error parsing row {row_idx}: {e}")
                    continue

            # Atomic swap — readers see either the old complete bank or the
            # new complete bank, never a half-parsed one.
            self.questions = new_questions
            self.setup_info_map = new_setup_info
            self.sheet_fingerprint = fingerprint
            self.loaded_at = time.time()
            self.is_loaded = True
            print(f"✅ Loaded {len(self.questions)} questions from sheet")

        except Exception as e:
            print(f"❌ Error loading questions: {e}")
            raise

    def _drive_search_id(self, uid):
        """Last-resort resolver: search Drive by filename for `uid` (+ common
        image extensions). Rescues images whose parent folder the scan can't
        traverse (e.g. the file is shared with the service account but its drive
        folder is not). Results get cached into file_map by the caller."""
        if not get_drive_service() or not uid:
            return None
        for nm in (uid + '.png', uid + '.jpg', uid + '.jpeg', uid):
            safe = nm.replace('\\', '\\\\').replace("'", "\\'")
            try:
                r = get_drive_service().files().list(
                    q=f"name = '{safe}' and trashed=false",
                    fields='files(id, name)', pageSize=1,
                    includeItemsFromAllDrives=True, supportsAllDrives=True,
                ).execute()
                fs = r.get('files', [])
                if fs:
                    print(f"      [drive_search] matched '{nm}' -> {fs[0]['id']}")
                    return fs[0]['id']
            except Exception as e:
                print(f"      [drive_search] error for {nm}: {e}")
        return None

    def resolve_file_id(self, potential_file_id: str) -> Optional[str]:
        """
        Resolve a potential file ID or filename to an actual Google Drive file ID.
        If it's already a valid file ID (starts with digit, contains alphanumeric), return it.
        If it's a filename, look it up in file_map and get the actual file ID.
        """
        if not potential_file_id:
            return None


        # If it looks like a real Google Drive file ID (contains hyphen/underscore, mostly alphanumeric)
        # and exists in file_map as a key, it's a file ID
        # Try exact then lowercase, with and without common extensions.
        # file_map holds both original- and lower-case keys, so checking the
        # lowercased form makes the whole lookup case-insensitive.
        for base in [potential_file_id, potential_file_id.lower()]:
            if base in self.file_map:
                result = self.file_map[base]
                return result
            for ext in ['.png', '.jpg', '.jpeg', '.gif']:
                if base + ext in self.file_map:
                    result = self.file_map[base + ext]
                    return result

        # Not in the pre-scanned map — search Drive by name. This rescues
        # images whose folder the scan can't traverse (parent not shared).
        found = self._drive_search_id(potential_file_id)
        if found:
            self.file_map[potential_file_id] = found
            self.file_map[potential_file_id.lower()] = found
            return found

        # Still nothing — assume the value is already a real file ID.
        return potential_file_id

    def get_image_url(self, uid: str) -> Optional[str]:
        """Get Google Drive image URL by UID/filename using pre-loaded file map"""
        if uid in self.image_url_cache:
            return self.image_url_cache[uid]

        if not get_drive_service() or not self.file_map:
            return None

        try:
            # List of filename variations to try (in order of likelihood)
            filenames_to_try = [
                uid + '.png',      # Most common
                uid + '.jpg',
                uid + '.jpeg',
                uid,               # Try without extension
                uid.lower() + '.png',  # Case-insensitive
                uid.lower() + '.jpg',
            ]

            for filename in filenames_to_try:
                # Check in pre-loaded file map (case-insensitive)
                file_id = self.file_map.get(filename) or self.file_map.get(filename.lower())

                if file_id:
                    # Create a direct download URL
                    image_url = f"https://drive.google.com/uc?id={file_id}&export=download"
                    self.image_url_cache[uid] = image_url
                    print(f"    ✅ Found: {filename} → image_url set")
                    return image_url

            print(f"  ⚠️  No image found for: {uid}")
            print(f"      Available files in folder: {', '.join(list(self.file_map.keys())[:10])}...")
            return None

        except Exception as e:
            print(f"  ❌ Error fetching image for UID {uid}: {e}")
            return None

    def get_unique_subtopics(self) -> List[str]:
        """Get all unique subtopics (excluding 'Question setup')"""
        self.ensure_fresh()

        subtopics = set()
        for q in self.questions:
            if q.subtopic and q.subtopic.lower() != 'question setup':
                subtopics.add(q.subtopic)

        return sorted(list(subtopics))

    def get_unique_difficulties(self) -> List[str]:
        """Get all unique difficulties"""
        self.ensure_fresh()

        difficulties = set()
        for q in self.questions:
            if q.difficulty:
                difficulties.add(q.difficulty)

        return sorted(list(difficulties))

    def get_unique_levels(self) -> List[str]:
        """Get all unique levels (streams/subjects)"""
        self.ensure_fresh()

        levels = set()
        for q in self.questions:
            if q.level:
                levels.add(q.level)

        return sorted(list(levels))

    def get_unique_subjects(self) -> List[str]:
        """Get all unique subjects (defaults to ['Physics'] when no Subject column)."""
        self.ensure_fresh()
        subjects = set()
        for q in self.questions:
            if q.subject:
                subjects.add(q.subject)
        return sorted(list(subjects)) or ['Physics']

    def get_filtered_questions(self, difficulty: Optional[str] = None,
                               subtopic: Optional[str] = None,
                               level: Optional[str] = None,
                               subject: Optional[str] = None) -> List[Question]:
        """Get questions filtered by difficulty and subtopic"""
        self.ensure_fresh()

        filtered = [q for q in self.questions]

        # Note: Setup rows are already excluded during loading

        # Filter by difficulty
        if difficulty:
            filtered = [q for q in filtered if q.difficulty.lower() == difficulty.lower()]

        # Filter by subtopic
        if subtopic:
            filtered = [q for q in filtered if q.subtopic.lower() == subtopic.lower()]

        # Filter by level. Accepts the 'pure' / 'nonpure' category keywords
        # (non-pure == the sheet's '4E5N') or an exact Level value.
        if level:
            req_key = _level_key(level)
            filtered = [q for q in filtered if _level_matches(req_key, q.level)]

        # Filter by subject (Physics, Math, ...)
        if subject:
            filtered = [q for q in filtered if q.subject and q.subject.lower() == subject.lower()]

        return filtered

# Initialize cache
cache = QuestionCache()

# ============================================================================
# CATEGORIZATION UTILITIES
# ============================================================================

def categorize_all_questions() -> Dict[str, List[Question]]:
    """Get all questions organized by type (TEXT, TABLE, IMAGE)"""
    cache.ensure_fresh()

    categorized = {
        'TEXT': [],
        'TABLE': [],
        'IMAGE': []
    }

    for question in cache.questions:
        # Setup rows are already excluded during loading
        categorized[question.option_type].append(question)

    return categorized


def get_questions_by_type(qtype: str) -> List[Question]:
    """Get questions of a specific type"""
    categorized = categorize_all_questions()
    return categorized.get(qtype.upper(), [])


def get_category_statistics() -> dict:
    """Get statistics on question categories"""
    categorized = categorize_all_questions()
    total = sum(len(q) for q in categorized.values())

    stats = {}
    for qtype, questions in categorized.items():
        by_difficulty = defaultdict(int)
        by_subtopic = defaultdict(int)
        table_column_count = 0

        for q in questions:
            by_difficulty[q.difficulty] += 1
            by_subtopic[q.subtopic] += 1

            # Track table columns for TABLE type
            if qtype == 'TABLE' and q.table_headers:
                table_column_count = len(q.table_headers)

        stats[qtype] = {
            'count': len(questions),
            'percentage': round((len(questions) / total * 100), 1) if total > 0 else 0,
            'by_difficulty': dict(sorted(by_difficulty.items())),
            'by_subtopic': dict(sorted(by_subtopic.items())),
            'has_images': any(q.image_url for q in questions)
        }

    return stats

# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(
    title="HabitGo API",
    description="Create filtered quizzes from Google Sheet database",
    version="1.0.0"
)

# Add CORS middleware to allow requests from your React frontend.
# Comma-separated list of allowed origins from CORS_ORIGINS env var.
# In dev (no env set) we default to localhost:5173.
_cors_origins_env = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000")
ALLOWED_ORIGINS = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
print(f"🌐 CORS allowed origins: {ALLOWED_ORIGINS}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {"status": "ok", "questions_loaded": cache.is_loaded}


@app.post("/api/admin/refresh")
def admin_refresh(
    authorization: str = Header(None),
    x_refresh_key: str = Header(None),
    key: Optional[str] = None,
):
    """Force an immediate reload of the question bank from the Sheet.

    Use right after editing the Sheet instead of waiting out the auto-refresh
    TTL (QUESTIONS_CACHE_TTL_SECONDS, default 5 min). Also clears the image
    URL cache and rescans the Drive file map in the background, so images
    re-uploaded under new Drive IDs resolve again.

    Auth: a valid teacher JWT (Authorization: Bearer ...), OR — if the
    ADMIN_REFRESH_KEY env var is set — a matching ?key= param / X-Refresh-Key
    header (handy for curl / a Sheets Apps Script hook without a login)."""
    provided_key = (key or x_refresh_key or '').strip()
    if ADMIN_REFRESH_KEY and provided_key == ADMIN_REFRESH_KEY:
        pass  # shared-secret auth OK
    else:
        require_teacher(authorization)  # raises 401/403 if not a teacher

    try:
        count = cache.refresh()
        return {
            "status": "ok",
            "questions_loaded": count,
            "file_map_rescan": "started in background",
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Reload failed: {e}")

# 6091 Physics (O-Level) syllabus topic sequences. Pure and Combined physics
# have different topic sets / orders; these drive the build-form filter order.
PURE_TOPIC_ORDER = [
    "Physical Quantities, Units and Measurement",
    "Kinematics",
    "Dynamics",
    "Turning Effect of Forces",
    "Pressure",
    "Energy",
    "Kinetic Particle Model of Matter",
    "Thermal Processes",
    "Thermal Properties of Matter",
    "General Properties of Waves",
    "Sound",
    "Electromagnetic Spectrum",
    "Light",
    "Static Electricity",
    "Current of Electricity",
    "D.C. Circuits",
    "Practical Electricity",
    "Magnetism",
    "Electromagnetism",
    "Electromagnetic Induction",
    "Radioactivity",
]

COMBINED_TOPIC_ORDER = [
    # SEAB 5086/87/88 official content structure — 16 topics, in order.
    "Physical Quantities, Units and Measurement",   # 1
    "Kinematics",                                    # 2
    "Force and Pressure",                            # 3
    "Dynamics",                                      # 4
    "Turning Effect of Forces",                      # 5
    "Energy",                                        # 6
    "Kinetic Particle Model of Matter",              # 7
    "Thermal Processes",                             # 8
    "General Wave Properties",                       # 9
    "Electromagnetic Spectrum",                      # 10
    "Light",                                         # 11
    "Electric Charge and Current of Electricity",    # 12
    "D.C. Circuits",                                 # 13
    "Practical Electricity",                         # 14
    "Magnetism and Electromagnetism",                # 15
    "Radioactivity",                                 # 16
]

COMBINED_G2_TOPIC_ORDER = [
    # SEAB 5105/06/07 Normal (Academic) Science, Physics — 13 topics, in order.
    # Note vs G3: no Turning Effect of Forces, no Light, no Magnetism/Electromag.
    "Physical Quantities, Units and Measurement",   # 1
    "Kinematics",                                    # 2
    "Force and Pressure",                            # 3
    "Dynamics",                                      # 4
    "Energy",                                        # 5
    "Kinetic Particle Model of Matter",              # 6
    "Thermal Processes",                             # 7
    "General Wave Properties",                       # 8
    "Electromagnetic Spectrum",                      # 9
    "Electric Charge and Current of Electricity",    # 10
    "D.C. Circuits",                                 # 11
    "Practical Electricity",                         # 12
    "Radioactivity",                                 # 13
]

COMBINED_G1_TOPIC_ORDER = [
    # SEAB 5148 Normal (Technical) Science — full syllabus, 11 topics, in order.
    # I. Machines Around Us (Physics)
    "Energy",                                        # 1
    "Electricity",                                   # 2
    "Wave",                                          # 3
    "Effects of Force",                              # 4
    # II. Food Matters (Chemistry)
    "Sources of Food",                               # 5
    "Food Chemistry",                                # 6
    "Food Safety",                                   # 7
    # III. Our Body and Health (Biology)
    "Staying Healthy",                               # 8
    "Digestion",                                     # 9
    "Breathing",                                     # 10
    "Blood Circulation",                             # 11
]

# Map a level key to its official syllabus topic order.
_LEVEL_TOPIC_ORDER = {
    "pure":       PURE_TOPIC_ORDER,
    "combined":   COMBINED_TOPIC_ORDER,
    "combinedG3": COMBINED_TOPIC_ORDER,
    "combinedG2": COMBINED_G2_TOPIC_ORDER,
    "combinedG1": COMBINED_G1_TOPIC_ORDER,
    # P6 Math: no per-topic syllabus yet — the picker offers only
    # "All topics" (empty official list), and quiz creation with no topic
    # selected filters purely by level.
    "p6math":     [],
}

def _order_for_level(req_key):
    """Official syllabus topic order for a level key. Falls back to the full
    Combined set for any unknown non-pure key."""
    if req_key in _LEVEL_TOPIC_ORDER:
        return _LEVEL_TOPIC_ORDER[req_key]
    return PURE_TOPIC_ORDER if req_key == "pure" else COMBINED_TOPIC_ORDER


def _norm_topic(name):
    """Normalise a topic name for matching: strip a leading number prefix
    (e.g. "2. Kinematics", "1.4 Effects of Force", "3) Wave"), lowercase, trim."""
    s = str(name).strip()
    s = re.sub(r'^\d+(?:\.\d+)*[.\)\s:]*', '', s)
    return s.strip().lower()


def _canonical_topic_base(name):
    """Collapse the sheet's many inconsistent Topic spellings into ONE
    canonical syllabus topic, so the picker and the quiz filter agree.

    The Topic column mixes number prefixes ("1. Dynamics" vs "Dynamics"),
    abbreviations ("DC Circuits" vs "D.C. Circuits") and aliases
    ("Measurement", "Kinetic Model" ...). Without this, exact-match topic
    filtering scatters questions across duplicate labels and quiz creation
    finds too few (or zero) questions. Unrecognised topics pass through
    cleaned (number prefix stripped) so nothing is silently dropped."""
    raw = str(name or '').strip()
    s = _norm_topic(raw)               # number-prefix stripped + lowercased
    if not s:
        return raw

    def has(*subs):
        return any(sub in s for sub in subs)

    # Order matters: most specific checks first.
    if has('physical quantit', 'units and measurement') or s in ('measurement', 'measurements'):
        return "Physical Quantities, Units and Measurement"
    if has('kinematic'):
        return "Kinematics"
    if (has('mass') and has('weight') and has('densit')) or s == 'density':
        return "Mass, Weight and Density"
    if has('turning effect', 'moment'):
        return "Turning Effect of Forces"
    if has('pressure'):
        return "Pressure"
    if has('kinetic') and has('particle', 'model'):
        return "Kinetic Particle Model of Matter"
    if has('thermal propert'):
        return "Thermal Properties of Matter"
    if has('thermal process', 'transfer of thermal', 'thermal transfer', 'thermal radiation') or s == 'temperature':
        return "Thermal Processes"
    if has('electromagnetic spectrum', 'em spectrum'):
        return "Electromagnetic Spectrum"
    if has('electromagnetic induction', 'em induction'):
        return "Electromagnetic Induction"
    if has('electromagnetism') or (has('magnetism') and has('electro')):
        return "Electromagnetism"
    if has('magnetism'):
        return "Magnetism"
    if has('static electric', 'electrostatic'):
        return "Static Electricity"
    if has('practical electric') or (has('safe') and has('electric')):
        return "Practical Electricity"
    if 'circuit' in s and ('d.c' in s or 'dc ' in s or s.endswith('dc') or 'd c' in s):
        return "D.C. Circuits"
    if (has('current') and has('electric')) or 'current electricity' in s:
        return "Current of Electricity"
    if has('light'):
        return "Light"
    if s == 'sound':
        return "Sound"
    if has('wave'):
        return "General Properties of Waves"
    if has('radioactiv', 'nuclear'):
        return "Radioactivity"
    if has('energy') or (has('work') and has('power')):
        return "Energy"
    if has('dynamic') or s in ('force', 'forces'):
        return "Dynamics"
    # Unknown: return number-prefix-stripped original (Title-preserving).
    return re.sub(r'^\d+(?:\.\d+)*[.\)\s:]*', '', raw).strip() or raw


# Combined Sci Physics (5086/87/88) merges several Pure topics into one. Applied
# AFTER the base canonical so the picker + filter use the official 16-topic set.
_COMBINED_MERGE = {
    "Static Electricity":           "Electric Charge and Current of Electricity",
    "Current of Electricity":       "Electric Charge and Current of Electricity",
    "Magnetism":                    "Magnetism and Electromagnetism",
    "Electromagnetism":             "Magnetism and Electromagnetism",
    "Electromagnetic Induction":    "Magnetism and Electromagnetism",
    "Pressure":                     "Force and Pressure",
    "Sound":                        "General Wave Properties",
    "General Properties of Waves":  "General Wave Properties",
    "Thermal Properties of Matter": "Thermal Processes",
    "Mass, Weight and Density":     "Dynamics",
}


# Normal (Technical) 5148 physics buckets the granular O-Level topics into the
# 4 coarse 5148 topics (Energy / Electricity / Wave / Effects of Force), since
# the G1 question bank is tagged with O-Level topic names.
_NT_BUCKET = {
    "Energy": "Energy",
    "Kinetic Particle Model of Matter": "Energy",
    "Thermal Processes": "Energy",
    "Thermal Properties of Matter": "Energy",
    "Static Electricity": "Electricity",
    "Current of Electricity": "Electricity",
    "Electric Charge and Current of Electricity": "Electricity",
    "D.C. Circuits": "Electricity",
    "Practical Electricity": "Electricity",
    "Magnetism": "Electricity",
    "Electromagnetism": "Electricity",
    "Electromagnetic Induction": "Electricity",
    "Magnetism and Electromagnetism": "Electricity",
    "General Properties of Waves": "Wave",
    "General Wave Properties": "Wave",
    "Sound": "Wave",
    "Light": "Wave",
    "Electromagnetic Spectrum": "Wave",
    "Physical Quantities, Units and Measurement": "Effects of Force",
    "Kinematics": "Effects of Force",
    "Dynamics": "Effects of Force",
    "Turning Effect of Forces": "Effects of Force",
    "Pressure": "Effects of Force",
    "Force and Pressure": "Effects of Force",
    "Mass, Weight and Density": "Effects of Force",
    # Chemistry / Biology (5148 II. Food Matters + III. Our Body and Health).
    # Identity entries so sheet tags matching the official names — in ANY
    # case, thanks to the lowercase fallback in canonical_topic — count
    # toward the picker. Without these, chem/bio tags only matched on exact
    # case and everything else fell through as an unknown topic ("Soon").
    "Sources of Food": "Sources of Food",
    "Food Chemistry": "Food Chemistry",
    "Food Safety": "Food Safety",
    "Staying Healthy": "Staying Healthy",
    "Digestion": "Digestion",
    "Breathing": "Breathing",
    "Blood Circulation": "Blood Circulation",
    # Common aliases seen in tagging
    "Respiration": "Breathing",
    "Respiratory System": "Breathing",
    "Circulatory System": "Blood Circulation",
    "Digestive System": "Digestion",
}
_NT_BUCKET_L = {k.lower(): v for k, v in _NT_BUCKET.items()}


def canonical_topic(name, combined=False, level_key=None):
    """Canonical syllabus topic. For Combined physics (`combined=True`) the
    Pure-style topics are merged into the official 16-topic Combined set. For
    Normal-Technical G1 (`level_key='combinedG1'`) the granular topics are
    further bucketed into the 4 coarse 5148 topics."""
    base = _canonical_topic_base(name)
    if level_key == 'combinedG1':
        merged = _COMBINED_MERGE.get(base, base)
        return (_NT_BUCKET.get(base) or _NT_BUCKET.get(merged)
                or _NT_BUCKET_L.get(base.lower()) or _NT_BUCKET_L.get(merged.lower())
                or merged)
    if combined:
        return _COMBINED_MERGE.get(base, base)
    return base


def _topic_sort_key(name, order):
    """Sort key placing topics in the given syllabus `order`. Unknown topics
    fall to the end, alphabetically."""
    key = _norm_topic(name)
    for idx, canon in enumerate(order):
        if _norm_topic(canon) == key:
            return (idx, key)
    return (len(order), key)


def _is_nonpure(level_value):
    """Combined Science Physics. The sheet's Level column now tags combined
    questions as combinedG1 / combinedG2 / combinedG3 (was '4E5N'); Pure
    Physics is tagged 'Pure Physics'. Combined == anything that isn't Pure."""
    s = str(level_value or '').strip().lower()
    return ('combined' in s) or ('4e5n' in s) or ('nonpure' in s) or ('non-pure' in s)


def _level_key(value):
    """Canonical subject/level key for a Level string (request param OR sheet
    cell): 'pure', 'combinedG1', 'combinedG2', 'combinedG3', or generic
    'combined' (all tiers). Handles 'Pure Physics', 'combinedG3', legacy
    '4E5N' / 'nonpure'."""
    s = str(value or '').strip().lower().replace(' ', '').replace('-', '').replace('_', '')
    if 'combinedg1' in s or s == 'g1':
        return 'combinedG1'
    if 'combinedg2' in s or s == 'g2':
        return 'combinedG2'
    if 'combinedg3' in s or s == 'g3':
        return 'combinedG3'
    if 'pure' in s:
        return 'pure'
    if ('p6' in s and 'math' in s) or s in ('p6', 'psle', 'pslemath'):
        return 'p6math'
    if 'combined' in s or '4e5n' in s or 'nonpure' in s:
        return 'combined'
    return s


def _level_matches(req_key, q_level):
    """Does a question's Level satisfy a requested level key? A specific tier
    (combinedG1/2/3, pure) matches only that tier; the generic 'combined'
    matches any combined tier."""
    if req_key in (None, '', 'all'):
        return True
    qk = _level_key(q_level)
    if req_key == 'pure':
        return qk == 'pure'
    if req_key == 'combined':
        return qk.startswith('combined')   # was: != 'pure' — must not swallow p6math etc.
    return qk == req_key


@app.get("/api/subtopics", response_model=List[str])
def get_subtopics(level: str = None):
    """Available topics, optionally filtered to one physics level.
    `level` = 'pure' or 'nonpure' (non-pure == the sheet's '4E5N' Level)."""
    try:
        cache.ensure_fresh()

        cat = (level or '').strip().lower()
        if cat:
            req_key = _level_key(level)
            # Return the FULL official syllabus topic list for this level, in
            # order, so the picker always mirrors the syllabus even when a topic
            # has no questions yet. The frontend disables the empty ones using
            # /api/availability.
            subtopics = list(_order_for_level(req_key))
        else:
            subtopics = sorted(cache.get_unique_subtopics(),
                               key=lambda s: _topic_sort_key(s, PURE_TOPIC_ORDER))

        print(f"  ✅ /api/subtopics (level={cat or 'all'}) -> {len(subtopics)}")
        return subtopics
    except Exception as e:
        print(f"  ❌ Error in /api/subtopics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/availability")
def get_availability(level: str = None):
    """Per-topic difficulty availability for the build form. Returns
    {topic: {"easy": N, "medium": N, "hard": N}} -- the question count per
    topic per difficulty at the chosen level. Lets the frontend grey out a
    difficulty when the picked topics can't supply enough questions."""
    try:
        cache.ensure_fresh()

        cat = (level or '').strip().lower()
        req_key = _level_key(level) if cat else None
        order = _order_for_level(req_key) if req_key else None
        # A level with an EMPTY official list (e.g. p6math) has no per-topic
        # syllabus: report ALL its questions under one "All topics" bucket so
        # the frontend can still grey out difficulties/counts it can't fill.
        no_syllabus = req_key is not None and not order
        order_norms = {_norm_topic(c) for c in order} if order else None

        avail = {}
        for q in cache.questions:
            topic_label = 'All topics' if no_syllabus else q.subtopic
            if not no_syllabus and (not q.subtopic or q.subtopic.lower() == 'question setup'):
                continue
            if req_key is not None and not _level_matches(req_key, q.level):
                continue
            if order_norms is not None and _norm_topic(q.subtopic) not in order_norms:
                continue
            dk = str(q.difficulty or '').strip().lower()
            if dk.startswith('eas'):
                dk = 'easy'
            elif dk.startswith('med'):
                dk = 'medium'
            elif dk.startswith('har'):
                dk = 'hard'
            else:
                continue
            counts = avail.setdefault(topic_label, {'easy': 0, 'medium': 0, 'hard': 0})
            counts[dk] += 1

        result = avail
        print(f"  /api/availability (level={cat or 'all'}) -> {len(result)} topics")
        return result
    except Exception as e:
        print(f"  Error in /api/availability: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/difficulties", response_model=List[str])
def get_difficulties():
    """Get all available difficulty levels"""
    try:
        print(f"📌 /api/difficulties called. Cache loaded: {cache.is_loaded}, Questions count: {len(cache.questions)}")

        # Ensure questions are loaded
        if not cache.is_loaded:
            print("  ⚠️  Cache not loaded, loading now...")
            cache.load_questions()

        difficulties = cache.get_unique_difficulties()
        print(f"  ✅ Returning {len(difficulties)} difficulties: {difficulties}")
        return difficulties
    except Exception as e:
        print(f"  ❌ Error in /api/difficulties: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/levels", response_model=List[str])
def get_levels():
    """Get all available levels (streams/subjects)"""
    try:
        print(f"📌 /api/levels called. Cache loaded: {cache.is_loaded}, Questions count: {len(cache.questions)}")

        # Ensure questions are loaded
        if not cache.is_loaded:
            print("  ⚠️  Cache not loaded, loading now...")
            cache.load_questions()

        levels = cache.get_unique_levels()
        print(f"  ✅ Returning {len(levels)} levels: {levels}")
        return levels
    except Exception as e:
        print(f"  ❌ Error in /api/levels: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/quiz", response_model=QuizResponse)
def create_quiz(request: QuizRequest, authorization: str = Header(None)):
    """
    Create a quiz based on filters (requires authentication)

    Query parameters:
    - difficulty (optional): Filter by difficulty level
    - subtopic (optional): Filter by subtopic
    - count: Number of questions to return (default: 5)
    """
    try:
        # Verify authentication token
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")

        token = authorization.replace("Bearer ", "")
        payload = verify_jwt_token(token)

        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user_id = payload.get('user_id')
        print(f"📌 Quiz request from user {user_id}")

        # Validate count
        if request.count < 1:
            raise HTTPException(status_code=400, detail="count must be at least 1")

        # Normalize subtopics: combine the legacy `subtopic` field with the new
        # `subtopics` list. De-duplicate while preserving order. Cap at 3.
        topics = []
        if request.subtopics:
            topics = [s for s in request.subtopics if s and s.strip()]
        if request.subtopic and request.subtopic.strip() and request.subtopic not in topics:
            topics.append(request.subtopic)
        topics = list(dict.fromkeys(topics))[:3]

        # Validate that we don't have more topics than questions
        if len(topics) > request.count:
            raise HTTPException(
                status_code=400,
                detail=f"You picked {len(topics)} topics but only {request.count} question(s). "
                       f"Reduce topics or increase question count."
            )

        # Build the question pool. With multiple topics, distribute count across
        # them (random remainder allocation). With 0 or 1 topic, fall back to the
        # original single-filter path.
        if len(topics) > 1:
            # Random distribution: start with floor allocation, then assign the
            # remainder to randomly-chosen topics so no topic gets 0.
            base, extra = divmod(request.count, len(topics))
            allocations = {t: base for t in topics}
            for t in random.sample(topics, extra):
                allocations[t] += 1
            # Ensure every topic has >= 1 (only matters if base==0; we already
            # validated len(topics) <= count so this guarantees at least one).
            for t in topics:
                if allocations[t] == 0:
                    allocations[t] = 1

            selected_questions = []
            shortfall = []
            for topic, n in allocations.items():
                pool = cache.get_filtered_questions(
                    difficulty=request.difficulty,
                    subtopic=topic,
                    level=request.level,
                    subject=request.subject,
                )
                if len(pool) < n:
                    shortfall.append(f"{topic} (need {n}, have {len(pool)})")
                    continue
                selected_questions.extend(random.sample(pool, n))

            if shortfall:
                raise HTTPException(
                    status_code=400,
                    detail=f"Not enough questions for: {', '.join(shortfall)}"
                )

            # Trim/pad to exactly count (in case of rounding) and shuffle so
            # questions of the same topic don't cluster.
            selected_questions = selected_questions[:request.count]
            random.shuffle(selected_questions)
            filtered_questions = selected_questions  # for downstream code that uses it
        else:
            # Single topic (or none): original path
            single_topic = topics[0] if topics else None
            filtered_questions = cache.get_filtered_questions(
                difficulty=request.difficulty,
                subtopic=single_topic,
                level=request.level,
                subject=request.subject,
            )
            # "All topics" (no specific pick): restrict the pool to the chosen
            # level's syllabus topics so irrelevant topics never leak in.
            # An EMPTY official list (e.g. p6math — no per-topic syllabus yet)
            # means "no restriction": use the whole level pool, otherwise the
            # empty set would filter out every question.
            if not single_topic and request.level:
                _key = _level_key(request.level)
                _order = _order_for_level(_key)
                if _order:
                    _norms = {_norm_topic(c) for c in _order}
                    filtered_questions = [q for q in filtered_questions
                                          if _norm_topic(q.subtopic) in _norms]
            if not filtered_questions:
                raise HTTPException(
                    status_code=400,
                    detail=f"No questions found with difficulty='{request.difficulty}', subtopic='{single_topic}', level='{request.level}'"
                )
            if len(filtered_questions) < request.count:
                raise HTTPException(
                    status_code=400,
                    detail=f"Only {len(filtered_questions)} questions available, but {request.count} requested"
                )
            if single_topic:
                # One specific topic: plain random pick from its pool.
                selected_questions = random.sample(filtered_questions, request.count)
            else:
                # "All topics - random mix": spread evenly across DISTINCT topics
                # so an N-question quiz pulls from N different topics whenever the
                # pool has at least N topics. With fewer than N topics, every topic
                # is covered once before any repeats, and the remainder is spread as
                # evenly as possible (round-robin, capacity-aware).
                by_topic = {}
                for _q in filtered_questions:
                    _k = _norm_topic(_q.subtopic) if getattr(_q, "subtopic", None) else "mixed"
                    by_topic.setdefault(_k, []).append(_q)
                _keys = list(by_topic.keys())
                _caps = {t: len(by_topic[t]) for t in _keys}
                _alloc = {t: 0 for t in _keys}
                _remaining = request.count  # guaranteed <= len(filtered_questions)
                _order = _keys[:]
                while _remaining > 0:
                    random.shuffle(_order)
                    _progressed = False
                    for _t in _order:
                        if _remaining == 0:
                            break
                        if _alloc[_t] < _caps[_t]:
                            _alloc[_t] += 1
                            _remaining -= 1
                            _progressed = True
                    if not _progressed:
                        break
                selected_questions = []
                for _t, _n in _alloc.items():
                    if _n:
                        selected_questions.extend(random.sample(by_topic[_t], _n))
                random.shuffle(selected_questions)

        # Create deep copies of selected questions to avoid modifying cached originals
        from copy import deepcopy
        selected_questions = [deepcopy(q) for q in selected_questions]

        # Attach setup information and set image URLs for each question

        for question in selected_questions:

            # Try to find setup info (check with and without trailing dash)
            setup_uid = question.uid.rstrip('-')  # Remove trailing dash if present
            setup_info = cache.setup_info_map.get(setup_uid)

            if setup_info:
                # Prepend setup text to question text
                if setup_info['text']:
                    question.question_text = setup_info['text'] + "\n\n" + question.question_text
                else:
                    pass

            # Use setup diagram if question doesn't have its own
            if not question.diagram_file_id and setup_info and setup_info.get('file_id'):
                question.diagram_file_id = setup_info['file_id']
            elif not question.diagram_file_id:
                pass

            # Always set setup_image_url if diagram exists (for frontend fallback)
            if question.diagram_file_id:
                # Resolve potential filename to actual Google Drive file ID
                # NOTE: store the raw filename/UID in the image URL (not a
                # resolved Drive ID) so serve_image resolves it fresh each request
                # — survives image re-uploads that change the Drive file ID.
                actual_file_id = question.diagram_file_id
                if actual_file_id:
                    # Use backend image proxy endpoint
                    question.setup_image_url = f"{PUBLIC_BASE_URL}/api/image/{actual_file_id}"
                else:
                    pass

            # Set image_url based on question type:
            # - For IMAGE type: ONLY use options image (if it exists)
            # - For non-IMAGE type: Use setup diagram

            if question.option_type == 'IMAGE':
                # IMAGE type: only show options image if available
                if question.options_image_uid:
                    # Resolve options image UID to file ID
                    options_file_id = question.options_image_uid
                    if options_file_id:
                        # Use backend image proxy endpoint
                        question.image_url = f"{PUBLIC_BASE_URL}/api/image/{options_file_id}"
                    else:
                        pass
                else:
                    pass
            else:
                # Non-IMAGE type (TEXT, TABLE): use setup diagram for image_url
                if question.setup_image_url:
                    question.image_url = question.setup_image_url

        return QuizResponse(
            questions=selected_questions,
            count=len(selected_questions),
            filters={
                "difficulty": request.difficulty,
                "subtopic": request.subtopic
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/questions/by-category")
def get_questions_by_category():
    """
    Get all questions organized by type (TEXT, TABLE, IMAGE)
    Useful for Claude Code to extract questions by category
    """
    try:
        categorized = categorize_all_questions()
        return categorized
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/questions/type/{qtype}")
def get_questions_by_type_endpoint(qtype: str):
    """
    Get questions of a specific type (TEXT, TABLE, or IMAGE)
    Example: /api/questions/type/TABLE
    """
    qtype = qtype.upper()
    if qtype not in ['TEXT', 'TABLE', 'IMAGE']:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type: {qtype}. Must be TEXT, TABLE, or IMAGE"
        )

    try:
        questions = get_questions_by_type(qtype)

        return {
            'type': qtype,
            'count': len(questions),
            'questions': questions
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/statistics/categories")
def get_category_statistics_endpoint():
    """
    Get statistics on question categories
    Returns: count, percentage, breakdown by difficulty and subtopic
    """
    try:
        stats = get_category_statistics()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/questions-by-category")
def export_questions_by_category():
    """
    Export all questions organized by category
    Returns data suitable for external processing
    """
    try:
        categorized = categorize_all_questions()
        stats = get_category_statistics()

        return {
            'exported_at': str(__import__('datetime').datetime.now()),
            'statistics': stats,
            'questions': categorized
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Question diagrams on Google Drive never change, so once we've fetched the
# bytes we keep them in memory and never round-trip to Drive again. The
# response also tells the browser to cache them forever (immutable), so a
# returning user re-loads images straight from disk cache.
# LRU image cache bounded by BYTES (was: 300 entries with no size budget —
# 300 × ~1 MB scans could OOM a 512 MB instance). Lock because handlers now
# run in a threadpool.
_IMAGE_CACHE = OrderedDict()
_IMAGE_CACHE_MAX_BYTES = 80 * 1024 * 1024
_IMAGE_CACHE_BYTES = [0]
_IMAGE_CACHE_LOCK = threading.Lock()
_IMAGE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
    "Content-Disposition": "inline; filename=image.png",
}

def _image_cache_get(key):
    with _IMAGE_CACHE_LOCK:
        data = _IMAGE_CACHE.get(key)
        if data is not None:
            _IMAGE_CACHE.move_to_end(key)  # LRU touch
        return data

def _image_cache_put(key, data):
    with _IMAGE_CACHE_LOCK:
        if key in _IMAGE_CACHE:
            _IMAGE_CACHE_BYTES[0] -= len(_IMAGE_CACHE[key])
        _IMAGE_CACHE[key] = data
        _IMAGE_CACHE.move_to_end(key)
        _IMAGE_CACHE_BYTES[0] += len(data)
        while _IMAGE_CACHE_BYTES[0] > _IMAGE_CACHE_MAX_BYTES and len(_IMAGE_CACHE) > 1:
            _, evicted = _IMAGE_CACHE.popitem(last=False)
            _IMAGE_CACHE_BYTES[0] -= len(evicted)

def _sniff_media_type(data: bytes) -> str:
    """Correct Content-Type from magic bytes (was hardcoded image/png)."""
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return 'image/png'


# NOTE: {file_id:path} (not plain {file_id}) — question/paper names like
# "PHY-NASS2024-P1-Express/NA Combined-010-setup" contain a literal slash,
# which a single-segment param can never match (the request 404s at the
# router with FastAPI's generic "Not Found" before serve_image even runs).
# The :path converter lets the param swallow slashes. Uvicorn decodes %2F
# before routing, so encoding on the URL-building side would NOT fix this.
@app.get("/api/image/{file_id:path}")
def serve_image(file_id: str):
    """
    Backend image proxy endpoint.
    Serves Google Drive images, cached in memory after the first fetch.
    Bypasses Google Drive embedding restrictions.

    Accepts either a real Drive file ID (1Abc...) or a filename / UID
    (PHY-CHIJ2022-P1-Pure-033-). For filename inputs we resolve through
    cache.file_map before hitting Drive — otherwise Drive responds 404
    because get_media() needs the actual ID, never the display name.
    """
    try:
        # Fast path — already in the in-memory cache.
        cached = _image_cache_get(file_id)
        if cached is not None:
            return Response(content=cached, media_type=_sniff_media_type(cached), headers=_IMAGE_HEADERS)

        if not get_drive_service():
            raise HTTPException(status_code=500, detail="Google Drive service not initialized")

        # ── Resolve filename → Drive ID via the pre-loaded file_map ──
        # The /api/image route used to call get_media() with whatever
        # arrived in the URL. That works for real Drive IDs but 404s
        # when the URL holds a filename / UID. Now we try the file_map
        # first; if it has the input (with or without an extension, or
        # as a prefix), swap to the actual Drive ID before downloading.
        resolved_id = file_id
        if cache.file_map:
            fm = cache.file_map
            candidates = [file_id, file_id.lower()]
            for ext in ('', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.PNG', '.JPG'):
                for base in candidates:
                    key = base + ext
                    if key in fm:
                        resolved_id = fm[key]
                        print(f"  🔍 [serve_image] '{file_id}' → '{resolved_id}' via file_map[{key!r}]")
                        break
                if resolved_id != file_id:
                    break

            # Prefix-match fallback — handles the trailing-hyphen case
            # where the DB has "PHY-CHIJ2022-P1-Pure-033-" but Drive has
            # "PHY-CHIJ2022-P1-Pure-033-Setup.png". We pick the FIRST
            # match by name (stable enough for setup/options diagrams).
            if resolved_id == file_id and len(file_id) >= 6:
                prefix_l = file_id.lower()
                hits = [(k, v) for k, v in fm.items() if k.startswith(prefix_l)]
                if hits:
                    hits.sort(key=lambda kv: (len(kv[0]), kv[0]))
                    resolved_id = hits[0][1]
                    print(f"  🔍 [serve_image] '{file_id}' → '{resolved_id}' via prefix '{hits[0][0]}' ({len(hits)} candidates)")

        # Download file from Google Drive. If the resolved value still isn't a
        # fetchable ID (e.g. the URL carried a filename that the scan never put
        # in file_map, or a stale ID), fall back to a Drive name-search by the
        # ORIGINAL request value and retry once. This makes the endpoint
        # self-healing for un-scanned per-paper folders.
        def _fetch(fid):
            dl = get_drive_service().files().get_media(fileId=fid).execute()
            if isinstance(dl, bytes):
                return dl
            buf = BytesIO()
            while True:
                chunk = dl.read(8192)
                if not chunk:
                    break
                buf.write(chunk)
            return buf.getvalue()

        try:
            data = _fetch(resolved_id)
        except Exception:
            searched = cache._drive_search_id(file_id)
            if not searched and resolved_id != file_id:
                searched = cache._drive_search_id(resolved_id)
            if searched and searched != resolved_id:
                print(f"  🔁 [serve_image] '{file_id}' fetch failed; drive-search → {searched}")
                cache.file_map[file_id] = searched
                cache.file_map[file_id.lower()] = searched
                resolved_id = searched
                data = _fetch(resolved_id)
            else:
                raise

        # Store in the cache under BOTH the original URL key and the
        # resolved Drive ID so subsequent requests with either form hit
        # the fast path.
        _image_cache_put(file_id, data)
        if resolved_id != file_id:
            _image_cache_put(resolved_id, data)

        return Response(content=data, media_type=_sniff_media_type(data), headers=_IMAGE_HEADERS)

    except HTTPException:
        raise
    except Exception as e:
        # Include both the requested key AND the resolved ID in the log
        # so it's obvious whether the lookup found anything.
        print(f"❌ Error serving image {file_id} (resolved → {resolved_id}): {e}")
        raise HTTPException(status_code=404, detail=f"Could not load image '{file_id}': {str(e)}")


# ============================================================================
# AUTHENTICATION ENDPOINTS
# ============================================================================

@app.post("/api/auth/signup", response_model=AuthResponse)
def signup(request: SignupRequest):
    """Register a new user with email and password"""
    try:
        # Validate input
        if not request.email or not request.password or not request.name:
            raise HTTPException(status_code=400, detail="Missing required fields")

        if len(request.password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Check if user already exists
            cursor.execute("SELECT id FROM users WHERE email = %s", (request.email,))
            existing_user = cursor.fetchone()

            if existing_user:
                raise HTTPException(status_code=400, detail="Email already registered")

            # Hash password
            password_hash = hash_password(request.password)

            # Insert new user
            cursor.execute(
                "INSERT INTO users (email, password_hash, name, school, student_class, teacher) VALUES (%s, %s, %s, %s, %s, %s)",
                (request.email, password_hash, request.name, request.school, request.student_class, request.teacher)
            )
            conn.commit()

            user_id = cursor.lastrowid

            # Create JWT token — new signups are always students; teachers are
            # promoted manually in the DB.
            token = create_jwt_token(user_id, request.email, is_teacher=False)

            print(f"✅ New user registered: {request.email}")

            return AuthResponse(
                success=True,
                message="Account created successfully",
                token=token,
                user={
                    'id': user_id,
                    'email': request.email,
                    'name': request.name,
                    'avatar_url': None,
                    'xp':         0,
                    'gems':       0,
                    'daily_goal': 10,
                    'level':      compute_level(0),
                    'rank':       compute_rank(0),
                    'is_teacher': False,
                    'school':        request.school,
                    'student_class': request.student_class,
                    'teacher':       request.teacher,
                }
            )

        finally:
            cursor.close()
            conn.close()

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Signup error: {e}")
        raise HTTPException(status_code=500, detail=f"Signup failed: {str(e)}")


@app.post("/api/auth/login", response_model=AuthResponse)
def login(request: LoginRequest):
    """Login with email and password"""
    try:
        if not request.email or not request.password:
            raise HTTPException(status_code=400, detail="Email and password required")

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Find user by email
            cursor.execute(
                "SELECT id, email, password_hash, name, avatar_url, xp, gems, daily_goal, is_teacher, school, student_class, teacher FROM users WHERE email = %s",
                (request.email,)
            )
            user = cursor.fetchone()

            if not user:
                raise HTTPException(status_code=401, detail="Invalid email or password")

            user_id, email, password_hash, name, avatar_url, user_xp, user_gems, user_daily_goal, user_is_teacher, u_school, u_class, u_teacher = user

            # Verify password
            if not verify_password(request.password, password_hash):
                raise HTTPException(status_code=401, detail="Invalid email or password")

            # Create JWT token — is_teacher is baked into the claim so the
            # frontend can route to the teacher dashboard without re-fetching.
            token = create_jwt_token(user_id, email, bool(user_is_teacher))

            print(f"✅ User logged in: {email}")

            return AuthResponse(
                success=True,
                message="Login successful",
                token=token,
                user={
                    'id': user_id,
                    'email': email,
                    'name': name,
                    'avatar_url': avatar_url,
                    'xp':         int(user_xp or 0),
                    'gems':       int(user_gems or 0),
                    'daily_goal': int(user_daily_goal or 10),
                    'level':      compute_level(user_xp or 0),
                    'rank':       compute_rank(user_xp or 0),
                    'is_teacher': bool(user_is_teacher),
                    'school':        u_school,
                    'student_class': u_class,
                    'teacher':       u_teacher,
                }
            )

        finally:
            cursor.close()
            conn.close()

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Login error: {e}")
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")


@app.post("/api/auth/google", response_model=AuthResponse)
def google_login(request: GoogleLoginRequest):
    """Login or register with Google OAuth token"""
    try:
        if not GOOGLE_CLIENT_ID:
            raise HTTPException(status_code=500, detail="Google OAuth not configured")

        # Verify Google token
        try:
            idinfo = id_token.verify_oauth2_token(request.token, requests.Request(), GOOGLE_CLIENT_ID)
            google_id = idinfo['sub']
            email = idinfo['email']
            name = idinfo.get('name', email.split('@')[0])
        except Exception as e:
            print(f"❌ Invalid Google token: {e}")
            raise HTTPException(status_code=401, detail="Invalid Google token")

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Check if user exists by google_id
            cursor.execute(
                "SELECT id, email, name, avatar_url, xp, gems, daily_goal FROM users WHERE google_id = %s",
                (google_id,)
            )
            user = cursor.fetchone()

            avatar_url = None
            if user:
                # Existing Google user
                user_id, user_email, user_name, avatar_url, google_xp, google_gems, google_daily_goal = user
                print(f"✅ Google user logged in: {user_email}")
            else:
                # Check if email exists (from other signup method)
                cursor.execute(
                    "SELECT id, name, avatar_url, xp, gems, daily_goal FROM users WHERE email = %s",
                    (email,)
                )
                existing = cursor.fetchone()

                if existing:
                    # Link Google account to existing email
                    user_id, user_name, avatar_url, google_xp, google_gems, google_daily_goal = existing
                    cursor.execute(
                        "UPDATE users SET google_id = %s WHERE id = %s",
                        (google_id, user_id)
                    )
                    conn.commit()
                    print(f"✅ Google account linked to existing user: {email}")
                else:
                    # New Google user
                    cursor.execute(
                        "INSERT INTO users (email, name, google_id) VALUES (%s, %s, %s)",
                        (email, name, google_id)
                    )
                    conn.commit()
                    user_id = cursor.lastrowid
                    user_name = name
                    google_xp = 0
                    google_gems = 0
                    google_daily_goal = 10
                    print(f"✅ New Google user registered: {email}")

            # Fetch is_teacher once so the existing-user, linked-user, and
            # brand-new-user branches above all converge on the same value.
            cursor.execute("SELECT is_teacher, school, student_class, teacher FROM users WHERE id = %s", (user_id,))
            _row = cursor.fetchone()
            google_is_teacher = bool(_row[0]) if _row else False
            g_school  = _row[1] if _row else None
            g_class   = _row[2] if _row else None
            g_teacher = _row[3] if _row else None

            # Create JWT token
            token = create_jwt_token(user_id, email, google_is_teacher)

            return AuthResponse(
                success=True,
                message="Google login successful",
                token=token,
                user={
                    'id': user_id,
                    'email': email,
                    'name': user_name,
                    'avatar_url': avatar_url,
                    'xp':         int(google_xp or 0),
                    'gems':       int(google_gems or 0),
                    'daily_goal': int(google_daily_goal or 10),
                    'level':      compute_level(google_xp or 0),
                    'rank':       compute_rank(google_xp or 0),
                    'is_teacher': google_is_teacher,
                    'school':        g_school,
                    'student_class': g_class,
                    'teacher':       g_teacher,
                }
            )

        finally:
            cursor.close()
            conn.close()

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Google login error: {e}")
        raise HTTPException(status_code=500, detail=f"Google login failed: {str(e)}")


@app.get("/api/auth/me")
def get_user_profile(authorization: str = Header(None)):
    """Get current user profile (requires token in Authorization header)"""
    try:
        # Get token from header
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")

        token = authorization.replace("Bearer ", "")

        # Verify token
        payload = verify_jwt_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user_id = payload.get('user_id')
        email = payload.get('email')

        # Get user details from database
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "SELECT id, email, name, avatar_url, created_at, is_teacher, equipped, school, student_class, teacher FROM users WHERE id = %s",
                (user_id,)
            )
            user = cursor.fetchone()

            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            (user_id, user_email, user_name, user_avatar, created_at,
             user_is_teacher, user_equipped, me_school, me_class, me_teacher) = user

            return {
                'success': True,
                'user': {
                    'id': user_id,
                    'email': user_email,
                    'name': user_name,
                    'avatar_url': user_avatar,
                    'created_at': str(created_at),
                    'is_teacher': bool(user_is_teacher),
                    # Include equipped wearables so every screen that
                    # rehydrates from /api/auth/me (Layout, HomePage,
                    # Settings, etc.) can render the avatar with hat /
                    # glasses / hands / legs / accessory / frame.
                    'equipped': _parse_equipped(user_equipped),
                    'school':        me_school,
                    'student_class': me_class,
                    'teacher':       me_teacher,
                }
            }

        finally:
            cursor.close()
            conn.close()

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error getting user profile: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/api/auth/complete-profile")
def complete_profile(request: CompleteProfileRequest, authorization: str = Header(None)):
    """Set the signed-in user's school / class / teacher. Powers the
    'complete your profile' gate for Google signups and legacy accounts that
    were created before these fields existed."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No authorization token")
    payload = verify_jwt_token(authorization.replace("Bearer ", ""))
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    school        = (request.school or '').strip()
    student_class = (request.student_class or '').strip()
    teacher       = (request.teacher or '').strip()
    if not school or not student_class or not teacher:
        raise HTTPException(status_code=400, detail="School, class and teacher are all required")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE users SET school = %s, student_class = %s, teacher = %s WHERE id = %s",
            (school, student_class, teacher, payload.get('user_id'))
        )
        conn.commit()
        print(f"✅ Profile completed for user {payload.get('email')}: {school} / {student_class} / {teacher}")
        return {'success': True, 'school': school, 'student_class': student_class, 'teacher': teacher}
    finally:
        cursor.close()
        conn.close()


class ProfileUpdateRequest(BaseModel):
    """Update the current user's display name and/or avatar."""
    name: Optional[str] = None
    avatar_url: Optional[str] = None  # data URL or external URL; None = leave unchanged


@app.put("/api/auth/profile")
def update_user_profile(request: ProfileUpdateRequest, authorization: str = Header(None)):
    """Update the current user's display name and avatar."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get('user_id')

        # Build a dynamic SET clause so we only touch provided fields.
        updates = []
        params = []
        if request.name is not None:
            name = request.name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="Name cannot be empty")
            if len(name) > 255:
                raise HTTPException(status_code=400, detail="Name is too long (max 255 chars)")
            updates.append("name = %s")
            params.append(name)
        if request.avatar_url is not None:
            avatar = request.avatar_url.strip()
            # Cap avatar payload at ~1.5MB to keep the DB sane (data URLs are bulky).
            if len(avatar) > 1_500_000:
                raise HTTPException(status_code=400, detail="Avatar image is too large (max ~1MB)")
            updates.append("avatar_url = %s")
            params.append(avatar or None)

        if not updates:
            raise HTTPException(status_code=400, detail="Nothing to update")

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            params.append(user_id)
            cursor.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE id = %s",
                tuple(params),
            )
            conn.commit()

            cursor.execute(
                "SELECT id, email, name, avatar_url, created_at FROM users WHERE id = %s",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="User not found")
            uid, email, name, avatar_url, created_at = row
            return {
                'success': True,
                'user': {
                    'id': uid,
                    'email': email,
                    'name': name,
                    'avatar_url': avatar_url,
                    'created_at': str(created_at),
                },
            }
        finally:
            cursor.close()
            conn.close()

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error updating profile: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# ============================================================================
# QUIZ HISTORY ENDPOINTS
# ============================================================================

@app.post("/api/quiz/submit")
def submit_quiz_attempt(request: QuizSubmissionRequest, authorization: str = Header(None)):
    """Submit a completed quiz and save to history"""
    try:
        # Verify authentication
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")

        token = authorization.replace("Bearer ", "")
        payload = verify_jwt_token(token)

        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user_id = payload.get('user_id')

        # Score is computed SERVER-SIDE from the per-question grading below when
        # full questions are provided — the same grade_answer() call that bakes
        # is_correct into questions_data. This guarantees the attempt-level
        # score/percentage (teacher tiles, weakest topics, drill-in rows) can
        # never disagree with the per-question ✓/✗ flags (attempt review).
        # The frontend-claimed score is only used as a cross-check + fallback.
        if request.score is not None and request.percentage is not None:
            score = request.score
            percentage = request.percentage
        else:
            # Fallback: calculate score from filtered questions (for backwards compatibility)
            filtered_questions = cache.get_filtered_questions(
                difficulty=request.difficulty,
                subtopic=request.subtopic
            )

            if not filtered_questions or len(filtered_questions) < request.count:
                raise HTTPException(status_code=400, detail="Could not retrieve quiz questions")

            score = 0
            for idx, user_answer in request.user_answers.items():
                try:
                    question_idx = int(idx)
                    if question_idx < len(filtered_questions):
                        question = filtered_questions[question_idx]
                        correct_answer = question.answer.strip()
                        if grade_answer(user_answer, correct_answer, question.options):
                            score += 1
                except (ValueError, IndexError):
                    continue

            percentage = round((score / request.count) * 100) if request.count > 0 else 0

        # Save to database
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            import json

            # Store full questions (with is_correct field) for retakes.
            # The skinny `questions_review` shape used to be persisted here, but it
            # stripped options/images and broke retakes — see get_quiz_for_retake().
            if request.questions:
                # Use frontend-provided questions; grade each one server-side.
                full_questions_data = []
                server_score = 0
                for idx, q in enumerate(request.questions):
                    user_answer = request.user_answers.get(idx, "")
                    # Fall back to correct_answer for retake rows whose payload
                    # carries the answer under that field instead of 'answer'.
                    correct_answer = (q.get('answer') or q.get('correct_answer') or "").strip()
                    is_correct = grade_answer(user_answer, correct_answer, q.get('options'))
                    server_score += int(is_correct)
                    q_copy = q.copy()
                    q_copy['is_correct'] = is_correct
                    q_copy['user_answer'] = user_answer
                    q_copy['correct_answer'] = correct_answer
                    full_questions_data.append(q_copy)
                full_questions_json = json.dumps(full_questions_data)

                # Server-side score is authoritative — it is derived from the
                # exact flags stored above, so every stat stays consistent.
                n_q = len(request.questions)
                server_pct = round((server_score / n_q) * 100) if n_q else 0
                if request.score is not None and int(request.score) != server_score:
                    print(f"⚠️  Score mismatch user={user_id}: frontend claimed "
                          f"{request.score}/{n_q}, server graded {server_score}/{n_q} "
                          f"— storing server value")
                score = server_score
                percentage = server_pct
                print(f"✅ Graded {n_q} questions server-side: {score}/{n_q} ({percentage}%)")
            else:
                # Fallback: serialize filtered Question objects with correctness
                filtered_questions = cache.get_filtered_questions(
                    difficulty=request.difficulty,
                    subtopic=request.subtopic
                )
                full_questions_data = []
                for idx, q in enumerate(filtered_questions[:request.count]):  # Only store the count requested
                    user_answer = request.user_answers.get(idx, "")
                    correct_answer = q.answer.strip()
                    is_correct = grade_answer(user_answer, correct_answer, q.options)
                    full_questions_data.append({
                        'uid': q.uid,
                        'qno': q.qno,
                        'subtopic': q.subtopic,
                        'difficulty': q.difficulty,
                        'level': q.level,
                        'question_text': q.question_text,
                        'options': q.options,
                        'answer': q.answer,
                        'correct_answer': correct_answer,
                        'user_answer': user_answer,
                        'option_type': q.option_type,
                        'table_headers': q.table_headers,
                        'table_header_levels': q.table_header_levels,
                        'table_header_colspan': q.table_header_colspan,
                        'table_rows': q.table_rows,
                        'diagram_file_id': q.diagram_file_id,
                        'options_image_uid': q.options_image_uid,
                        'image_url': q.image_url,
                        'setup_image_url': q.setup_image_url,
                        'is_correct': is_correct
                    })
                full_questions_json = json.dumps(full_questions_data)
                print(f"✅ Storing {len(full_questions_data)} questions from filtered set with correctness")

            # Resolve the parent attempt for retakes.
            # If the request claims to be a retake of attempt X, walk up to the root
            # original so all retakes of a saved quiz share the same parent_attempt_id.
            # Also fetch the parent's name so the retake inherits it (frontend cannot
            # rename a quiz at retake time — name lives on the original).
            parent_attempt_id = None
            inherited_name = None
            if request.parent_attempt_id:
                cursor.execute("""
                    SELECT id, parent_attempt_id, name FROM quiz_attempts
                    WHERE id = %s AND user_id = %s
                """, (request.parent_attempt_id, user_id))
                row = cursor.fetchone()
                if row:
                    parent_attempt_id = row[1] if row[1] else row[0]
                    # If the referenced row is itself a retake, look up the root's name
                    if row[1]:
                        cursor.execute(
                            "SELECT name FROM quiz_attempts WHERE id = %s AND user_id = %s",
                            (row[1], user_id),
                        )
                        root = cursor.fetchone()
                        inherited_name = root[0] if root else row[2]
                    else:
                        inherited_name = row[2]

            # Decide the name to store on this row.
            # Retakes inherit; originals use the request name or a sensible default.
            if parent_attempt_id is not None:
                quiz_name = inherited_name
            else:
                quiz_name = (request.name or "").strip() or None
                if not quiz_name:
                    parts = []
                    if request.subtopic: parts.append(request.subtopic)
                    if request.difficulty: parts.append(request.difficulty)
                    parts.append(f"{request.count}Q")
                    quiz_name = " · ".join(parts)

            cursor.execute("""
                INSERT INTO quiz_attempts
                (user_id, name, difficulty, subtopic, score, percentage, total_questions,
                 time_spent_seconds, questions_data, parent_attempt_id, quiz_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id,
                quiz_name,
                request.difficulty or None,
                request.subtopic or None,
                score,
                percentage,
                request.count,
                request.time_spent_seconds,
                full_questions_json,
                parent_attempt_id,
                (request.quiz_type or 'practice').lower(),
            ))
            conn.commit()
            attempt_id = cursor.lastrowid

            kind = "retake" if parent_attempt_id else "saved quiz"
            print(f"✅ Quiz attempt saved ({kind}): user={user_id}, score={score}/{request.count}, "
                  f"time={request.time_spent_seconds}s, parent={parent_attempt_id}")

            # Reward awards are DAILY-MODE ONLY. Practice quizzes (request.quiz_type
            # != 'daily') save the attempt for retakes but grant no XP, no gems,
            # and do NOT count toward the daily goal or streak.
            is_daily = ((request.quiz_type or 'practice').lower() == 'daily')

            # Defaults — used by practice and as fall-throughs if any daily branch errors.
            xp_breakdown   = {"base": 0, "perfect": 0, "diff_mult": 1.0,
                              "daily_goal": 0, "streak_milestone": 0}
            xp_base        = 0
            xp_perfect     = 0
            xp_pre         = 0
            xp_delta       = 0
            xp_total       = 0
            rank_up        = False
            new_rank       = None
            gems_delta     = 0
            gems_total     = 0
            gems_breakdown = {"correct": 0, "quiz": 0, "rank_up": 0}
            daily_progress = None
            streak_awarded = False
            user_daily_goal = DAILY_CORRECT_TARGET

            if is_daily:
                # XP base + perfect-score bonus.
                xp_breakdown = xp_for_quiz(score, request.count, request.difficulty)
                xp_base    = xp_breakdown["base"]
                xp_perfect = xp_breakdown["perfect"]

                # Pre-submit XP snapshot for rank-up detection.
                try:
                    cursor.execute("SELECT xp FROM users WHERE id = %s", (user_id,))
                    _r = cursor.fetchone()
                    xp_pre = int(_r[0]) if _r and _r[0] is not None else 0
                except Exception:
                    xp_pre = 0

                # Defined OUTSIDE the try — the XP-banking upsert below needs
                # these even when the daily-credit block errors.
                today_d = _effective_today(user_id)
                _daily_subject = 'Physics'

                # Daily-progress credit + streak award.
                try:
                    # Daily goal is fixed at 10 correct — no per-user setting.
                    user_daily_goal = DAILY_CORRECT_TARGET
                    _prev_p, _now_p, _today_correct, _today_total = _credit_daily_practice(
                        cursor, conn, user_id, _daily_subject, today_d, score, request.count,
                        target=user_daily_goal,
                    )
                    _current_streak = None
                    _longest_streak = None
                    _freeze_used = False
                    if _now_p and not _prev_p:
                        _current_streak, _longest_streak, _freezes, _freeze_used = _award_streak_day(
                            cursor, conn, user_id, today_d
                        )
                        streak_awarded = True
                    else:
                        try:
                            cursor.execute(
                                "SELECT current_streak, longest_streak FROM streaks WHERE user_id = %s",
                                (user_id,),
                            )
                            _srow = cursor.fetchone()
                            if _srow:
                                _current_streak, _longest_streak = _srow[0], _srow[1]
                        except Exception:
                            pass
                    daily_progress = {
                        'today_correct': _today_correct,
                        'target': user_daily_goal,
                        'today_total': _today_total,
                        'passed_today': _now_p,
                        'streak_awarded': streak_awarded,
                        'freeze_used': _freeze_used,
                        'current_streak': _current_streak,
                        'longest_streak': _longest_streak,
                    }
                except Exception as _e:
                    print(f"\u26a0\ufe0f Daily-progress credit failed (non-fatal): {_e}")

                # StarQuest bonuses that depend on daily/streak state.
                xp_daily_goal = XP_BONUS_DAILY_GOAL if streak_awarded else 0
                xp_streak_milestone = 0
                if streak_awarded and _current_streak and _current_streak > 0 \
                        and _current_streak % XP_BONUS_STREAK_EVERY == 0:
                    xp_streak_milestone = XP_BONUS_STREAK_AMOUNT

                xp_breakdown.update({
                    "daily_goal":       xp_daily_goal,
                    "streak_milestone": xp_streak_milestone,
                })
                xp_delta = xp_base + xp_perfect + xp_daily_goal + xp_streak_milestone

                # XP commit + rank-up detection.
                xp_total = xp_pre
                try:
                    if xp_delta > 0:
                        cursor.execute(
                            "UPDATE users SET xp = xp + %s WHERE id = %s",
                            (xp_delta, user_id),
                        )
                        # Bank the same XP onto today's daily row so the
                        # daily / weekly leaderboards can rank by XP.
                        # ATOMIC UPSERT — a plain UPDATE silently no-ops (and
                        # the XP vanishes from the daily/weekly boards) when
                        # the row doesn't exist yet, e.g. if the daily-credit
                        # block above errored. score/total 0 so a row created
                        # here never distorts the daily-goal tally.
                        cursor.execute(
                            "INSERT INTO daily_challenges "
                            "(user_id, subject, challenge_date, score, total, "
                            " percentage, passed, attempts, xp) "
                            "VALUES (%s, %s, %s, 0, 0, 0, FALSE, 0, %s) "
                            "ON DUPLICATE KEY UPDATE xp = xp + VALUES(xp)",
                            (user_id, _daily_subject, today_d, xp_delta),
                        )
                        conn.commit()
                    cursor.execute("SELECT xp FROM users WHERE id = %s", (user_id,))
                    _xp = cursor.fetchone()
                    xp_total = int(_xp[0]) if _xp and _xp[0] is not None else 0

                    pre_rank  = compute_rank(xp_pre)
                    post_rank = compute_rank(xp_total)
                    if post_rank["tier_index"] > pre_rank["tier_index"]:
                        rank_up  = True
                        new_rank = post_rank
                except Exception as _xe:
                    print(f"\u26a0\ufe0f XP award failed (non-fatal): {_xe}")

                # Gem award.
                gems_delta, gems_breakdown = gems_for_quiz(score, rank_up)
                try:
                    if gems_delta > 0:
                        cursor.execute("UPDATE users SET gems = gems + %s WHERE id = %s",
                                       (gems_delta, user_id))
                        conn.commit()
                    cursor.execute("SELECT gems FROM users WHERE id = %s", (user_id,))
                    _gr2 = cursor.fetchone()
                    gems_total = int(_gr2[0]) if _gr2 and _gr2[0] is not None else 0
                except Exception as _ge:
                    print(f"\u26a0\ufe0f Gem award failed (non-fatal): {_ge}")
            else:
                # Practice: still report the current XP/gems balance so the UI stays accurate,
                # but never modify either column.
                try:
                    cursor.execute("SELECT xp, gems FROM users WHERE id = %s", (user_id,))
                    _r = cursor.fetchone()
                    xp_total   = int(_r[0]) if _r and _r[0] is not None else 0
                    gems_total = int(_r[1]) if _r and _r[1] is not None else 0
                    xp_pre = xp_total
                except Exception:
                    pass

            progression = compute_progression(xp_total)

            return {
                'success': True,
                'attempt_id': attempt_id,
                'score': score,
                'percentage': percentage,
                'total_questions': request.count,
                'message': f'Quiz saved! You scored {score}/{request.count} ({percentage}%)',
                'daily_progress': daily_progress,
                'xp_delta':       xp_delta,
                'xp_total':       xp_total,
                'xp_breakdown':   xp_breakdown,
                'gems_delta':     gems_delta,
                'gems_total':     gems_total,
                'gems_breakdown': gems_breakdown,
                'progression':    progression,
                'daily_goal':     user_daily_goal,
                'rank_up':        rank_up,
                'new_rank':       new_rank,
            }

        finally:
            cursor.close()
            conn.close()

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error submitting quiz: {e}")
        raise HTTPException(status_code=500, detail=f"Error saving quiz: {str(e)}")


@app.get("/api/history")
def get_quiz_history(
    authorization: str = Header(None),
    saved_only: bool = False,
):
    """Get quiz attempts for the current user.

    Query params:
      - saved_only (bool): If true, return only original "saved quizzes"
        (attempts where parent_attempt_id IS NULL). Retakes are excluded.
        Default false returns every attempt — used by the History tab.
    """
    try:
        # Verify authentication
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")

        token = authorization.replace("Bearer ", "")
        payload = verify_jwt_token(token)

        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user_id = payload.get('user_id')

        # Get quiz history from database
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Get attempts (optionally filtered to originals only)
            sql = """
                SELECT id, name, difficulty, subtopic, score, percentage, total_questions,
                       time_spent_seconds, attempted_at, parent_attempt_id
                FROM quiz_attempts
                WHERE user_id = %s
            """
            if saved_only:
                # Saved = original practice quizzes only. Exclude retakes AND
                # daily-challenge attempts. Legacy rows with a NULL quiz_type
                # predate the daily feature, so they count as practice.
                sql += " AND parent_attempt_id IS NULL"
                sql += " AND (quiz_type IS NULL OR quiz_type <> 'daily')"
            sql += " ORDER BY attempted_at DESC"

            cursor.execute(sql, (user_id,))
            attempts = cursor.fetchall()

            # Build a lookup for parent rows so retakes can resolve their
            # quiz name and a 1-based attempt number within the parent group.
            cursor.execute(
                "SELECT id, name FROM quiz_attempts WHERE user_id = %s AND parent_attempt_id IS NULL",
                (user_id,),
            )
            parent_name_by_id = {row[0]: row[1] for row in cursor.fetchall()}

            # Compute attempt_number per quiz group: oldest attempt = #1.
            cursor.execute(
                """
                SELECT id, COALESCE(parent_attempt_id, id) AS root_id, attempted_at
                FROM quiz_attempts
                WHERE user_id = %s
                ORDER BY COALESCE(parent_attempt_id, id), attempted_at ASC, id ASC
                """,
                (user_id,),
            )
            attempt_number_by_id = {}
            attempt_count_by_root = {}
            for row in cursor.fetchall():
                aid, root_id, _ = row
                attempt_count_by_root[root_id] = attempt_count_by_root.get(root_id, 0) + 1
                attempt_number_by_id[aid] = attempt_count_by_root[root_id]

            # Format results
            history = []
            total_score = 0

            for attempt in attempts:
                (attempt_id, name, difficulty, subtopic, score, percentage,
                 total_q, time_spent, attempted_at, parent_attempt_id) = attempt
                root_id = parent_attempt_id if parent_attempt_id else attempt_id
                quiz_name = name or parent_name_by_id.get(root_id) or f"Quiz #{root_id}"
                history.append({
                    'id': attempt_id,
                    'name': quiz_name,
                    'difficulty': difficulty,
                    'subtopic': subtopic,
                    'score': score,
                    'percentage': percentage,
                    'total_questions': total_q,
                    'time_spent_seconds': time_spent,
                    'attempted_at': str(attempted_at),
                    'parent_attempt_id': parent_attempt_id,
                    'is_retake': parent_attempt_id is not None,
                    'attempt_number': attempt_number_by_id.get(attempt_id, 1),
                    'attempt_count': attempt_count_by_root.get(root_id, 1),
                })
                total_score += percentage or 0

            average_score = round(total_score / len(attempts)) if attempts else 0

            return {
                'success': True,
                'attempts': history,
                'total_attempts': len(attempts),
                'average_score': average_score
            }

        finally:
            cursor.close()
            conn.close()

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching quiz history: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/api/history/{attempt_id}")
def get_attempt_details(attempt_id: int, authorization: str = Header(None)):
    """Get detailed review of a specific quiz attempt (shows wrong answers)"""
    try:
        # Verify authentication
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")

        token = authorization.replace("Bearer ", "")
        payload = verify_jwt_token(token)

        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user_id = payload.get('user_id')

        # Get attempt from database
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT id, difficulty, subtopic, score, percentage, total_questions,
                       time_spent_seconds, questions_data, attempted_at
                FROM quiz_attempts
                WHERE id = %s AND user_id = %s
            """, (attempt_id, user_id))

            attempt = cursor.fetchone()

            if not attempt:
                raise HTTPException(status_code=404, detail="Quiz attempt not found")

            attempt_id, difficulty, subtopic, score, percentage, total_q, time_spent, questions_json, attempted_at = attempt

            import json
            questions_data = json.loads(questions_json) if questions_json else []

            # Re-hydrate legacy skinny rows from the cache so review has options + images.
            # Same logic as get_quiz_for_retake — see that endpoint for the full rationale.
            if any(not q.get('options') and not q.get('table_rows') for q in questions_data):
                if not cache.is_loaded:
                    cache.load_questions()
                by_qno = {q.qno: q for q in cache.questions if q.qno}
                by_text = {q.question_text.strip(): q for q in cache.questions if q.question_text}

                for i, q in enumerate(questions_data):
                    if q.get('options') or q.get('table_rows'):
                        continue
                    full = (by_qno.get(q.get('qno'))
                            or by_text.get((q.get('question_text') or '').strip()))
                    if not full:
                        continue
                    questions_data[i] = {
                        'qno': full.qno,
                        'uid': full.uid,
                        'subtopic': full.subtopic,
                        'difficulty': full.difficulty,
                        'level': full.level,
                        'question_text': full.question_text,
                        'options': full.options,
                        'answer': full.answer,
                        'option_type': full.option_type,
                        'table_headers': full.table_headers,
                        'table_header_levels': full.table_header_levels,
                        'table_header_colspan': full.table_header_colspan,
                        'table_rows': full.table_rows,
                        'diagram_file_id': full.diagram_file_id,
                        'options_image_uid': full.options_image_uid,
                        'image_url': full.image_url,
                        'setup_image_url': full.setup_image_url,
                        'explanation': full.explanation,
                        'index': q.get('index', i),
                        'user_answer': q.get('user_answer'),
                        'correct_answer': q.get('correct_answer') or full.answer,
                        'is_correct': q.get('is_correct', False),
                    }

            # Resolve diagram / options image file IDs to URLs
            for q in questions_data:
                if q.get('diagram_file_id'):
                    actual_file_id = q['diagram_file_id']
                    if actual_file_id:
                        q['setup_image_url'] = f"{PUBLIC_BASE_URL}/api/image/{actual_file_id}"

                if q.get('option_type') == 'IMAGE' and q.get('options_image_uid'):
                    options_file_id = q['options_image_uid']
                    if options_file_id:
                        q['image_url'] = f"{PUBLIC_BASE_URL}/api/image/{options_file_id}"
                elif q.get('setup_image_url') and not q.get('image_url'):
                    q['image_url'] = q['setup_image_url']

            # Filter to show only wrong answers (safely handle missing is_correct field)
            wrong_answers = [q for q in questions_data if not q.get('is_correct', False)]

            return {
                'success': True,
                'attempt': {
                    'id': attempt_id,
                    'difficulty': difficulty,
                    'subtopic': subtopic,
                    'score': score,
                    'percentage': percentage,
                    'total_questions': total_q,
                    'time_spent_seconds': time_spent,
                    'attempted_at': str(attempted_at),
                    'wrong_answers': wrong_answers,
                    'wrong_count': len(wrong_answers)
                }
            }

        finally:
            cursor.close()
            conn.close()

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching attempt details: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")



@app.get("/api/history/{attempt_id}/quiz")
def get_quiz_for_retake(attempt_id: int, authorization: str = Header(None)):
    """Get the full quiz questions from a previous attempt for retaking"""
    try:
        # Verify authentication
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")

        token = authorization.replace("Bearer ", "")
        payload = verify_jwt_token(token)

        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user_id = payload.get('user_id')

        # Get quiz from database
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT id, difficulty, subtopic, score, percentage, total_questions,
                       time_spent_seconds, questions_data, attempted_at
                FROM quiz_attempts
                WHERE id = %s AND user_id = %s
            """, (attempt_id, user_id))

            attempt = cursor.fetchone()

            if not attempt:
                raise HTTPException(status_code=404, detail="Quiz attempt not found")

            attempt_id, difficulty, subtopic, score, percentage, total_q, time_spent, questions_json, attempted_at = attempt

            import json
            questions_data = json.loads(questions_json) if questions_json else []

            # Re-hydrate legacy "skinny" rows that lack full question fields.
            # Older versions of /api/quiz/submit stored only the review shape:
            #   {index, question_text, user_answer, correct_answer, is_correct, subtopic, difficulty}
            # so options / option_type / table_rows / image refs were never persisted.
            # On retake we look the row up in the in-memory question cache and merge
            # the full fields back in, preserving the attempt-specific answer data.
            if any(not q.get('options') and not q.get('table_rows') for q in questions_data):
                if not cache.is_loaded:
                    cache.load_questions()
                by_qno = {q.qno: q for q in cache.questions if q.qno}
                by_text = {q.question_text.strip(): q for q in cache.questions if q.question_text}

                for i, q in enumerate(questions_data):
                    if q.get('options') or q.get('table_rows'):
                        continue  # already full
                    full = (by_qno.get(q.get('qno'))
                            or by_text.get((q.get('question_text') or '').strip()))
                    if not full:
                        print(f"⚠️  Could not rehydrate question {i}: no cache match for "
                              f"qno={q.get('qno')!r} text={(q.get('question_text') or '')[:60]!r}")
                        continue
                    questions_data[i] = {
                        'qno': full.qno,
                        'uid': full.uid,
                        'subtopic': full.subtopic,
                        'difficulty': full.difficulty,
                        'level': full.level,
                        'question_text': full.question_text,
                        'options': full.options,
                        'answer': full.answer,
                        'option_type': full.option_type,
                        'table_headers': full.table_headers,
                        'table_header_levels': full.table_header_levels,
                        'table_header_colspan': full.table_header_colspan,
                        'table_rows': full.table_rows,
                        'diagram_file_id': full.diagram_file_id,
                        'options_image_uid': full.options_image_uid,
                        'image_url': full.image_url,
                        'setup_image_url': full.setup_image_url,
                        'explanation': full.explanation,
                        # preserve attempt-specific fields from the saved row
                        'user_answer': q.get('user_answer'),
                        'correct_answer': q.get('correct_answer') or full.answer,
                        'is_correct': q.get('is_correct', False),
                    }

            # Set image URLs for questions
            for q in questions_data:
                if q.get('diagram_file_id'):
                    actual_file_id = q['diagram_file_id']
                    if actual_file_id:
                        q['setup_image_url'] = f"{PUBLIC_BASE_URL}/api/image/{actual_file_id}"

                if q.get('option_type') == 'IMAGE' and q.get('options_image_uid'):
                    options_file_id = q['options_image_uid']
                    if options_file_id:
                        q['image_url'] = f"{PUBLIC_BASE_URL}/api/image/{options_file_id}"
                elif q.get('setup_image_url'):
                    q['image_url'] = q['setup_image_url']

            return {
                'success': True,
                'questions': questions_data,
                'count': len(questions_data),
                'filters': {
                    'difficulty': difficulty,
                    'subtopic': subtopic,
                    'level': None
                },
                'original_attempt': {
                    'score': score,
                    'percentage': percentage,
                    'attempted_at': str(attempted_at)
                }
            }

        finally:
            cursor.close()
            conn.close()

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching quiz for retake: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/api/stats")
def get_user_stats(authorization: str = Header(None)):
    """Aggregated statistics for the current user: overall accuracy,
    performance trend, per-subtopic + per-difficulty breakdown,
    weakest topics."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get("user_id")

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Stats cover both Practice and Daily Challenge attempts. Daily
            # quizzes (QuizMaker mode="daily") are stored in quiz_attempts with
            # quiz_type='daily' and carry the same per-question questions_data,
            # so every breakdown works for them just like practice attempts.
            cursor.execute(
                """
                SELECT id, difficulty, subtopic, score, percentage, total_questions,
                       time_spent_seconds, questions_data, attempted_at, parent_attempt_id
                FROM quiz_attempts
                WHERE user_id = %s AND quiz_type IN ('practice', 'daily')
                ORDER BY attempted_at ASC
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

        if not rows:
            return {
                "total_attempts": 0,
                "total_quizzes": 0,
                "total_questions_answered": 0,
                "total_correct": 0,
                "overall_accuracy": 0,
                "total_time_seconds": 0,
                "avg_time_per_question": 0,
                "trend": [],
                "by_subtopic": [],
                "by_difficulty": [],
                "weakest_subtopics": [],
                "recent_streak_days": 0,
                "growth": {
                    "this_week_accuracy": 0, "last_week_accuracy": 0,
                    "accuracy_delta": None, "topics_improved": 0,
                    "topics_tracked": 0, "per_topic_trend": [],
                },
                "first_attempt_accuracy": 0,
                "by_subject": [],
                "weekly_accuracy": [],
            }

        import json as _json
        total_attempts = len(rows)
        unique_quizzes = len({r[9] if r[9] else r[0] for r in rows})
        total_questions = sum(int(r[5] or 0) for r in rows)
        total_correct = sum(int(r[3] or 0) for r in rows)
        total_time = sum(int(r[6] or 0) for r in rows)
        overall_acc = round(100 * total_correct / total_questions, 1) if total_questions else 0

        trend = [
            {
                "attempted_at": str(r[8]),
                "percentage": int(r[4] or 0),
                "score": int(r[3] or 0),
                "total": int(r[5] or 0),
                "subtopic": r[2],
                "difficulty": r[1],
            }
            for r in rows[-15:]
        ]

        subtopic_agg = {}
        difficulty_agg = {}
        for r in rows:
            attempt_diff = r[1] or "Unknown"
            attempt_subtopic = r[2] or "Mixed"
            qjson = r[7]
            try:
                questions = _json.loads(qjson) if qjson else []
            except Exception:
                questions = []
            if questions:
                for q in questions:
                    if not isinstance(q, dict):
                        continue
                    is_correct = bool(q.get("is_correct"))
                    sub = q.get("subtopic") or attempt_subtopic or "Mixed"
                    diff = q.get("difficulty") or attempt_diff or "Unknown"
                    subtopic_agg.setdefault(sub, [0, 0])
                    subtopic_agg[sub][0] += int(is_correct)
                    subtopic_agg[sub][1] += 1
                    difficulty_agg.setdefault(diff, [0, 0])
                    difficulty_agg[diff][0] += int(is_correct)
                    difficulty_agg[diff][1] += 1
            else:
                subtopic_agg.setdefault(attempt_subtopic, [0, 0])
                subtopic_agg[attempt_subtopic][0] += int(r[3] or 0)
                subtopic_agg[attempt_subtopic][1] += int(r[5] or 0)
                difficulty_agg.setdefault(attempt_diff, [0, 0])
                difficulty_agg[attempt_diff][0] += int(r[3] or 0)
                difficulty_agg[attempt_diff][1] += int(r[5] or 0)

        def shape(agg):
            return sorted(
                [
                    {
                        "name": k,
                        "correct": v[0],
                        "total": v[1],
                        "accuracy": round(100 * v[0] / v[1], 1) if v[1] else 0,
                    }
                    for k, v in agg.items()
                    if v[1] > 0          # only topics/difficulties actually attempted
                ],
                key=lambda x: x["name"].lower(),
            )

        by_subtopic = shape(subtopic_agg)
        by_difficulty = shape(difficulty_agg)
        weakest = sorted(
            [s for s in by_subtopic if s["total"] >= 3],
            key=lambda s: s["accuracy"],
        )[:3]

        from datetime import date, timedelta, datetime
        attempt_dates = set()
        for r in rows:
            try:
                attempt_dates.add(r[8].date() if hasattr(r[8], "date") else None)
            except Exception:
                pass
        attempt_dates.discard(None)
        streak = 0
        d = date.today()
        while d in attempt_dates:
            streak += 1
            d -= timedelta(days=1)

        # ---- T3.2: Growth metrics (improvement-led, Practice + Daily) ----
        now_dt = datetime.now()
        topic_series = {}   # topic -> [is_correct, ...] in chronological order
        wk_recent = [0, 0]  # [correct, total] for attempts < 7 days old
        wk_prev = [0, 0]    # [correct, total] for attempts 7-14 days old
        for r in rows:
            ts = r[8]
            try:
                age_days = (now_dt - ts).days if ts is not None else None
            except Exception:
                age_days = None
            qjson = r[7]
            try:
                questions = _json.loads(qjson) if qjson else []
            except Exception:
                questions = []
            valid_qs = [q for q in questions if isinstance(q, dict)] if questions else []
            if valid_qs:
                for q in valid_qs:
                    ic = int(bool(q.get("is_correct")))
                    sub = q.get("subtopic") or (r[2] or "Mixed")
                    topic_series.setdefault(sub, []).append(ic)
                    if age_days is not None and age_days < 7:
                        wk_recent[0] += ic; wk_recent[1] += 1
                    elif age_days is not None and age_days < 14:
                        wk_prev[0] += ic; wk_prev[1] += 1
            else:
                # legacy skinny row: only attempt-level totals available
                c, t = int(r[3] or 0), int(r[5] or 0)
                if age_days is not None and age_days < 7:
                    wk_recent[0] += c; wk_recent[1] += t
                elif age_days is not None and age_days < 14:
                    wk_prev[0] += c; wk_prev[1] += t

        def _acc(pair):
            return round(100 * pair[0] / pair[1], 1) if pair[1] else 0.0

        this_week_acc = _acc(wk_recent)
        last_week_acc = _acc(wk_prev)
        accuracy_delta = (round(this_week_acc - last_week_acc, 1)
                          if (wk_recent[1] and wk_prev[1]) else None)

        per_topic_trend = []
        topics_improved = 0
        for sub, series in topic_series.items():
            n = len(series)
            if n < 4:
                continue  # not enough history to call a trend
            half = n // 2
            earlier = series[:half]
            recent = series[half:]
            e_acc = round(100 * sum(earlier) / len(earlier), 1)
            r_acc = round(100 * sum(recent) / len(recent), 1)
            delta = round(r_acc - e_acc, 1)
            if delta > 0:
                topics_improved += 1
            per_topic_trend.append({
                "name": sub,
                "earlier_accuracy": e_acc,
                "recent_accuracy": r_acc,
                "delta": delta,
                "questions": n,
            })
        per_topic_trend.sort(key=lambda x: x["delta"], reverse=True)

        growth = {
            "this_week_accuracy": this_week_acc,
            "last_week_accuracy": last_week_acc,
            "accuracy_delta": accuracy_delta,
            "topics_improved": topics_improved,
            "topics_tracked": len(per_topic_trend),
            "per_topic_trend": per_topic_trend,
        }

        # ---- Dashboard v2 — first-attempt accuracy, per-subject, weak-topic
        # enrichment (attempts / avg time / repeated mistakes) and a weekly
        # accuracy time-series for the trend chart. ----
        seen_uids = set()
        fa_correct = fa_total = 0          # first-attempt accuracy
        subject_agg = {}                   # subject -> [correct, total]
        topic_quizzes = {}                 # topic   -> set(attempt_id)
        topic_time = {}                    # topic   -> [summed_seconds, question_count]
        wrong_by_uid = {}                  # uid     -> times answered wrong
        uid_topic = {}                     # uid     -> topic
        week_agg = {}                      # 'YYYY-Www' -> [correct, total]

        for r in rows:
            attempt_id   = r[0]
            attempt_sub  = r[2] or "Mixed"
            attempt_time = int(r[6] or 0)
            ts           = r[8]
            try:
                _qs = _json.loads(r[7]) if r[7] else []
            except Exception:
                _qs = []
            valid_qs = [q for q in _qs if isinstance(q, dict)]
            n_q = len(valid_qs) if valid_qs else int(r[5] or 0)
            per_q_time = (attempt_time / n_q) if n_q else 0
            try:
                _iso = ts.isocalendar()
                wk = f"{_iso[0]}-W{int(_iso[1]):02d}"
            except Exception:
                wk = None

            if valid_qs:
                for q in valid_qs:
                    ic   = bool(q.get("is_correct"))
                    sub  = q.get("subtopic") or attempt_sub or "Mixed"
                    subj = q.get("subject") or "Physics"
                    uid  = q.get("uid")
                    subject_agg.setdefault(subj, [0, 0])
                    subject_agg[subj][0] += int(ic); subject_agg[subj][1] += 1
                    topic_quizzes.setdefault(sub, set()).add(attempt_id)
                    tt = topic_time.setdefault(sub, [0.0, 0])
                    tt[0] += per_q_time; tt[1] += 1
                    if uid:
                        uid_topic[uid] = sub
                        if uid not in seen_uids:
                            seen_uids.add(uid)
                            fa_total += 1; fa_correct += int(ic)
                        if not ic:
                            wrong_by_uid[uid] = wrong_by_uid.get(uid, 0) + 1
                    if wk:
                        wa = week_agg.setdefault(wk, [0, 0])
                        wa[0] += int(ic); wa[1] += 1
            else:
                c, t = int(r[3] or 0), int(r[5] or 0)
                subject_agg.setdefault("Physics", [0, 0])
                subject_agg["Physics"][0] += c; subject_agg["Physics"][1] += t
                topic_quizzes.setdefault(attempt_sub, set()).add(attempt_id)
                tt = topic_time.setdefault(attempt_sub, [0.0, 0])
                tt[0] += attempt_time; tt[1] += t
                if wk:
                    wa = week_agg.setdefault(wk, [0, 0])
                    wa[0] += c; wa[1] += t

        first_attempt_accuracy = round(100 * fa_correct / fa_total, 1) if fa_total else 0

        by_subject = sorted(
            [{"name": k, "correct": v[0], "total": v[1],
              "accuracy": round(100 * v[0] / v[1], 1) if v[1] else 0}
             for k, v in subject_agg.items() if v[1] > 0],
            key=lambda x: x["name"].lower(),
        )

        repeated_by_topic = {}
        for _uid, _wc in wrong_by_uid.items():
            if _wc >= 2:
                _t = uid_topic.get(_uid, "Mixed")
                repeated_by_topic[_t] = repeated_by_topic.get(_t, 0) + 1

        _trend_delta = {t["name"]: t["delta"] for t in per_topic_trend}

        def _enrich_topic(entry):
            nm = entry["name"]
            tt = topic_time.get(nm, [0.0, 0])
            entry["quizzes"]           = len(topic_quizzes.get(nm, set()))
            entry["avg_time"]          = round(tt[0] / tt[1], 1) if tt[1] else 0
            entry["repeated_mistakes"] = repeated_by_topic.get(nm, 0)
            entry["trend_delta"]       = _trend_delta.get(nm)
            return entry

        for _e in by_subtopic:   # weakest entries share these dict refs
            _enrich_topic(_e)

        weekly_accuracy = [
            {"week": _wk, "correct": _v[0], "total": _v[1],
             "accuracy": round(100 * _v[0] / _v[1], 1) if _v[1] else 0}
            for _wk, _v in sorted(week_agg.items())
        ][-8:]

        return {
            "total_attempts": total_attempts,
            "total_quizzes": unique_quizzes,
            "total_questions_answered": total_questions,
            "total_correct": total_correct,
            "overall_accuracy": overall_acc,
            "total_time_seconds": total_time,
            "avg_time_per_question": round(total_time / total_questions, 1) if total_questions else 0,
            "trend": trend,
            "by_subtopic": by_subtopic,
            "by_difficulty": by_difficulty,
            "weakest_subtopics": weakest,
            "recent_streak_days": streak,
            "growth": growth,
            "first_attempt_accuracy": first_attempt_accuracy,
            "by_subject": by_subject,
            "weekly_accuracy": weekly_accuracy,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error computing stats: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# ============================================================================
# PHASE 1: SUBJECTS, PLACEMENT QUIZ & RANKING
# ============================================================================

def answer_key(val) -> str:
    """Normalize an answer to a comparable key so option-text vs letter compares correctly.
    Mirrors the frontend answerKey() EXACTLY — the two MUST stay in sync or the
    stored per-question is_correct flags disagree with the score the student saw.
      "(3) 45 cm2" -> "3"   (PSLE numeric options "(1)"–"(4)")
      "C. lamp X"  -> "C",  "C" -> "C",  "C) foo" -> "C"
      "Density increases" -> "DENSITY INCREASES"  (delimiter after the letter is
      REQUIRED — a bare leading letter must not swallow sentence options)."""
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


def _option_letter_and_body(line: str, idx: int) -> Tuple[str, str]:
    """Split one option line into (letter/number label, body text), mirroring the
    frontend's option-label logic. Falls back to the positional letter (A-D)."""
    t = (line or "").strip()
    m = re.match(r'^\((\d+)\)\s*(.*)$', t)          # PSLE "(1) …"
    if m:
        return m.group(1), m.group(2)
    m = re.match(r'^([A-Da-d])[\.\)\:\-]?\s+(.*)$', t)  # "A. foo" / "A) foo"
    if m:
        return m.group(1).upper(), m.group(2)
    if re.fullmatch(r'[A-Da-d]', t):                # bare "A" (diagram options)
        return t.upper(), ""
    return chr(65 + idx), t                          # unlabelled sentence option


def grade_answer(user_answer, correct_answer, options=None) -> bool:
    """THE single source of truth for whether an answer is correct.

    1. Compare normalized keys (letter / PSLE number / full-text uppercase).
    2. If that fails and the question has an options list, resolve BOTH sides
       to an option label via the options (handles answer stored as full text
       while the pick is a letter, and vice versa) and compare labels.

    Every place that grades — submit, regrade script, stats — must call this,
    never raw string comparison."""
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
        return key  # already a label, or unresolvable — compare as-is

    ru = resolve(user_answer, uk)
    rc = resolve(correct_answer, ck)
    return bool(ru) and ru == rc


def score_to_band(percentage: float) -> str:
    """Map a percentage score to a Singapore O-Level grade band (A1 best, F9 worst)."""
    p = percentage
    if p >= 75: return "A1"
    if p >= 70: return "A2"
    if p >= 65: return "B3"
    if p >= 60: return "B4"
    if p >= 55: return "C5"
    if p >= 50: return "C6"
    if p >= 45: return "D7"
    if p >= 40: return "E8"
    return "F9"


@app.get("/api/subjects", response_model=List[str])
def get_subjects():
    """All subjects present in the question bank (defaults to [\'Physics\'])."""
    try:
        cache.ensure_fresh()
        return cache.get_unique_subjects()
    except Exception as e:
        print(f"\u274c Error in /api/subjects: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/placement/questions")
def get_placement_questions(subject: str = "Physics", authorization: str = Header(None)):
    """Return 15 placement questions for a subject, spread across topics and difficulty."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        if not verify_jwt_token(authorization.replace("Bearer ", "")):
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        cache.ensure_fresh()

        pool = cache.get_filtered_questions(subject=subject)
        if not pool:
            raise HTTPException(status_code=400, detail=f"No questions found for subject {subject!r}")

        TARGET = 15
        from collections import defaultdict
        by_diff = defaultdict(list)
        for q in pool:
            by_diff[(q.difficulty or "Unknown")].append(q)
        for bucket in by_diff.values():
            random.shuffle(bucket)

        # Round-robin across difficulty buckets for an even easy->hard spread.
        selected = []
        diff_keys = list(by_diff.keys())
        random.shuffle(diff_keys)
        idx = 0
        guard = 0
        while len(selected) < TARGET and any(by_diff[k] for k in diff_keys) and guard < 1000:
            k = diff_keys[idx % len(diff_keys)]
            if by_diff[k]:
                selected.append(by_diff[k].pop())
            idx += 1
            guard += 1
        if len(selected) < TARGET:
            leftover = [q for q in pool if q not in selected]
            random.shuffle(leftover)
            selected.extend(leftover[:TARGET - len(selected)])
        random.shuffle(selected)

        from copy import deepcopy
        selected = [deepcopy(q) for q in selected]
        for question in selected:
            # Attach setup info: some questions keep their diagram/text in a
            # separate "-setup" row, mapped in cache.setup_info_map.
            setup_uid = question.uid.rstrip("-")
            setup_info = cache.setup_info_map.get(setup_uid)
            if setup_info:
                if setup_info.get("text"):
                    question.question_text = setup_info["text"] + "\n\n" + question.question_text
                if not question.diagram_file_id and setup_info.get("file_id"):
                    question.diagram_file_id = setup_info["file_id"]

            # Resolve the setup diagram (always, if one exists)
            if question.diagram_file_id:
                actual_file_id = question.diagram_file_id
                if actual_file_id:
                    question.setup_image_url = f"{PUBLIC_BASE_URL}/api/image/{actual_file_id}"

            # Resolve the answer-options image for IMAGE questions; otherwise
            # fall back to the setup diagram as the question's image_url.
            if question.option_type == "IMAGE":
                if question.options_image_uid:
                    options_file_id = question.options_image_uid
                    if options_file_id:
                        question.image_url = f"{PUBLIC_BASE_URL}/api/image/{options_file_id}"
            else:
                if question.setup_image_url:
                    question.image_url = question.setup_image_url

        return {"subject": subject, "count": len(selected), "questions": selected}
    except HTTPException:
        raise
    except Exception as e:
        print(f"\u274c Error in /api/placement/questions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class PlacementSubmitRequest(BaseModel):
    subject: str = "Physics"
    score: int
    total: int


@app.post("/api/placement/submit")
def submit_placement(request: PlacementSubmitRequest, authorization: str = Header(None)):
    """Score a placement quiz, compute the F9-A1 band, store it as the starting rank."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get("user_id")

        if request.total <= 0:
            raise HTTPException(status_code=400, detail="total must be greater than 0")
        score = max(0, min(request.score, request.total))
        percentage = round(100 * score / request.total)
        band = score_to_band(percentage)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO user_subject_ranks (user_id, subject, rank_band, rank_score)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE rank_band = VALUES(rank_band), rank_score = VALUES(rank_score)
                """,
                (user_id, request.subject, band, percentage),
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

        return {
            "success": True,
            "subject": request.subject,
            "score": score,
            "total": request.total,
            "percentage": percentage,
            "rank_band": band,
            "tier_name": RANK_TIER_NAMES.get(band, ""),
            "tier_desc": RANK_TIER_DESC.get(band, ""),
            "tier_icon": RANK_TIER_ICONS.get(band, ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"\u274c Error in /api/placement/submit: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ranks")
def get_user_ranks(authorization: str = Header(None)):
    """Return the current user\'s rank per subject."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get("user_id")

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT subject, rank_band, rank_score, placed_at, updated_at
                FROM user_subject_ranks
                WHERE user_id = %s
                ORDER BY subject
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

        ranks = [
            {
                "subject": r[0],
                "rank_band": r[1],
                "rank_score": r[2],
                "tier_name": RANK_TIER_NAMES.get(r[1], ""),
                "tier_desc": RANK_TIER_DESC.get(r[1], ""),
                "tier_icon": RANK_TIER_ICONS.get(r[1], ""),
                "placed_at": str(r[3]),
                "updated_at": str(r[4]),
            }
            for r in rows
        ]
        # StarQuest progression (XP/Level/Rank) — included alongside the legacy
        # per-subject placement bands so a single request hydrates both the
        # placement gate AND the user-facing rank display.
        try:
            conn2 = get_db_connection()
            cursor2 = conn2.cursor()
            try:
                cursor2.execute("SELECT xp, gems, daily_goal, equipped FROM users WHERE id = %s", (user_id,))
                _xr = cursor2.fetchone()
                _xp   = int(_xr[0]) if _xr and _xr[0] is not None else 0
                _gems = int(_xr[1]) if _xr and _xr[1] is not None else 0
                _goal = int(_xr[2]) if _xr and _xr[2] is not None else 10
                _equipped = _parse_equipped(_xr[3] if _xr and len(_xr) > 3 else None)
                # Freeze count: 1 if no streak row yet (everyone starts with
                # the free weekly freeze, matching /api/streak's default).
                cursor2.execute("SELECT freezes_available FROM streaks WHERE user_id = %s", (user_id,))
                _fr = cursor2.fetchone()
                _freezes = int(_fr[0]) if _fr and _fr[0] is not None else 1
            finally:
                cursor2.close()
                conn2.close()
        except Exception:
            _xp, _gems, _goal, _freezes, _equipped = 0, 0, 10, 1, {}

        return {
            "ranks":             ranks,
            "has_placement":     len(ranks) > 0,
            "progression":       compute_progression(_xp),
            "gems":              _gems,
            "daily_goal":        _goal,
            "freezes_available": _freezes,
            "freeze_cap":        GEMS_FREEZE_CAP,
            "equipped":          _equipped,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"\u274c Error in /api/ranks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# PHASE 2: DAILY CHALLENGE, STREAK & RANK MOVEMENT
# ============================================================================

# Band order, worst -> best. Index in this list == rank strength.
RANK_BANDS = ["F9", "E8", "D7", "C6", "C5", "B4", "B3", "A2", "A1"]
BAND_FLOOR = {"A1": 75, "A2": 70, "B3": 65, "B4": 60, "C5": 55,
              "C6": 50, "D7": 45, "E8": 40, "F9": 35}
STREAK_FLOOR = 60          # fallback floor only (used if a user has no rank yet)
DAILY_CHALLENGE_LEN = 10   # questions per Daily Challenge
DAILY_CORRECT_TARGET = 10  # cumulative correct answers needed in a day to earn the streak


# ---------------------------------------------------------------------------
# XP economy (StarQuest §01). XP is permanent, awarded only from quizzes.
# Earning sources:
#   correct        +10   (per question)
#   wrong           0
#   perfect bonus  +20   (100% on a quiz of >=3 questions)
#   daily-goal     +15   (1x per day, when DAILY_CORRECT_TARGET is crossed)
#   streak milestone +50 (every 7 streak days)
# Difficulty multiplier applies to the base only (correct * 10), not to bonuses.
# ---------------------------------------------------------------------------
XP_BASE_PER_CORRECT     = 10
XP_DIFFICULTY_MULT      = {"easy": 1.0, "medium": 1.25, "hard": 1.5}
XP_BONUS_PERFECT        = 20
XP_BONUS_PERFECT_MIN_QS = 3
XP_BONUS_DAILY_GOAL     = 15
XP_BONUS_STREAK_AMOUNT  = 50
XP_BONUS_STREAK_EVERY   = 7


def xp_for_quiz(correct, total, difficulty):
    """Compute the XP breakdown for a quiz result.
      base    = correct * 10 * difficulty_mult  (rounded)
      perfect = 20 if 100% on >=3 questions else 0
    Daily-goal and streak-milestone bonuses are layered by the caller because
    they depend on cumulative state outside the quiz row.
    """
    correct = max(0, int(correct or 0))
    total   = max(0, int(total or 0))
    diff_key = (difficulty or "").strip().lower()
    diff_mult = XP_DIFFICULTY_MULT.get(diff_key, 1.0)
    base = int(round(correct * XP_BASE_PER_CORRECT * diff_mult))
    perfect = XP_BONUS_PERFECT if (total >= XP_BONUS_PERFECT_MIN_QS and correct == total and correct > 0) else 0
    return {"base": base, "perfect": perfect, "diff_mult": diff_mult}


# StarQuest §02 — Level: pure XP/50 staircase, derived (no schema).
def compute_level(xp):
    return int(max(0, int(xp or 0)) // 50) + 1


# StarQuest §03 — Rank tiers. XP-derived; replaces F9..A1 in the UI display.
STARQUEST_RANKS = [
    {"key": "cadet",        "name": "Cadet",        "xp_min":    0, "icon": "✨"},
    {"key": "pilot",        "name": "Pilot",        "xp_min":  200, "icon": "🚀"},
    {"key": "navigator",    "name": "Navigator",    "xp_min":  500, "icon": "🧭"},
    {"key": "commander",    "name": "Commander",    "xp_min": 1200, "icon": "🎖"},
    {"key": "captain",      "name": "Captain",      "xp_min": 2500, "icon": "🌟"},
    {"key": "star_admiral", "name": "Star Admiral", "xp_min": 5000, "icon": "⭐"},
]


def compute_rank(xp):
    """StarQuest rank dict. Carries both modern keys (name/icon) and legacy
    aliases (tier_name/tier_icon/rank_band) so existing components keep rendering."""
    xp = max(0, int(xp or 0))
    idx = 0
    for i, t in enumerate(STARQUEST_RANKS):
        if xp >= t["xp_min"]:
            idx = i
    cur = STARQUEST_RANKS[idx]
    nxt = STARQUEST_RANKS[idx + 1] if idx + 1 < len(STARQUEST_RANKS) else None
    return {
        "tier_index": idx,
        "key":        cur["key"],
        "name":       cur["name"],
        "icon":       cur["icon"],
        "xp_min":     cur["xp_min"],
        "xp_next":    nxt["xp_min"] if nxt else None,
        "next_name":  nxt["name"]  if nxt else None,
        "tier_name":  cur["name"],
        "tier_icon":  cur["icon"],
        "rank_band":  cur["key"],
    }


def compute_progression(xp):
    xp = max(0, int(xp or 0))
    return {"xp": xp, "level": compute_level(xp), "rank": compute_rank(xp)}


# ---------------------------------------------------------------------------
# StarQuest §05 — Crystals (gems). Spendable currency, earned with each quiz.
#   correct        +2
#   quiz completion +5  (once per submit, regardless of score)
#   rank-up        +50
#   weekly leaderboard top 1/2/3: +100/+60/+30  (Phase 2B)
# ---------------------------------------------------------------------------
GEMS_PER_CORRECT = 2
GEMS_PER_QUIZ    = 5
GEMS_RANK_UP     = 50
GEMS_FREEZE_COST = 30
GEMS_FREEZE_CAP  = 2


def gems_for_quiz(correct, rank_up):
    """Pure helper: (delta, breakdown_dict). Mirrors xp_for_quiz's shape."""
    correct = max(0, int(correct or 0))
    g_correct = correct * GEMS_PER_CORRECT
    g_quiz    = GEMS_PER_QUIZ
    g_rankup  = GEMS_RANK_UP if rank_up else 0
    return g_correct + g_quiz + g_rankup, {"correct": g_correct, "quiz": g_quiz, "rank_up": g_rankup}


def _award_streak_day(cursor, conn, user_id, today):
    """Idempotent: credit `today` toward the user's streak. Reusable across endpoints
    so any "I qualified for today" event can call it. Safe to call multiple times
    on the same day — no-op after the first credit.

    Freeze rules (per PHASE0_SPEC §5/§6):
    - Hard reset to 1 freeze on the first qualifying day of a new ISO calendar week
      (Mon-Sun). No stacking — last week's unused freeze is replaced, not added to.
    - When freeze covers a missed day, freeze_used_date records WHICH day it bridged.
    """
    from datetime import timedelta
    cursor.execute(
        "SELECT current_streak, longest_streak, last_qualified_date, "
        "freezes_available, freeze_last_granted, freeze_used_date FROM streaks WHERE user_id = %s",
        (user_id,),
    )
    srow = cursor.fetchone()
    if not srow:
        cursor.execute(
            "INSERT INTO streaks (user_id, current_streak, longest_streak, "
            "freezes_available, freeze_last_granted) VALUES (%s, 0, 0, 1, %s)",
            (user_id, today),
        )
        conn.commit()
        current_streak, longest_streak, last_qualified = 0, 0, None
        freezes, freeze_granted, freeze_used_date = 1, today, None
    else:
        current_streak, longest_streak, last_qualified, freezes, freeze_granted, freeze_used_date = srow

    # Weekly freeze policy: at the start of a new ISO week, top the user up to
    # a FLOOR of 1 free freeze. No stacking — a user who still holds a freeze
    # from last week gets nothing extra. They can still buy +1 via
    # /api/freeze/purchase, up to GEMS_FREEZE_CAP.
    if freeze_granted is None or today.isocalendar()[:2] != freeze_granted.isocalendar()[:2]:
        freezes = max(freezes or 0, 1)
        freeze_granted = today

    freeze_used = False
    if last_qualified == today:
        # Already credited today — no-op (streak stays where it is).
        pass
    else:
        if last_qualified is None:
            current_streak = 1
        else:
            missed = (today - last_qualified).days - 1
            if missed <= 0:
                current_streak += 1
            elif missed <= freezes:
                freezes -= missed
                freeze_used = True
                # Record WHICH day was bridged (the one right before `today`).
                # Cap-1 freeze means only 1 missed day can be bridged at a time.
                freeze_used_date = today - timedelta(days=1)
                current_streak += 1
            else:
                current_streak = 1
        longest_streak = max(longest_streak, current_streak)
        last_qualified = today

    cursor.execute(
        "UPDATE streaks SET current_streak = %s, longest_streak = %s, "
        "last_qualified_date = %s, freezes_available = %s, "
        "freeze_last_granted = %s, freeze_used_date = %s WHERE user_id = %s",
        (current_streak, longest_streak, last_qualified, freezes, freeze_granted, freeze_used_date, user_id),
    )
    conn.commit()
    return current_streak, longest_streak, freezes, freeze_used


def _credit_daily_practice(cursor, conn, user_id, subject, today, correct, total, target=None):
    """Add today's quiz result into the daily cumulative tally.
    `target` defaults to DAILY_CORRECT_TARGET if not supplied; in normal use
    the caller passes the user's configured daily_goal so 10/15/20 are honored.
    Returns (prev_passed, now_passed, today_correct, today_total)."""
    if target is None:
        target = DAILY_CORRECT_TARGET
    cursor.execute(
        "SELECT score, total, passed FROM daily_challenges "
        "WHERE user_id = %s AND subject = %s AND challenge_date = %s",
        (user_id, subject, today),
    )
    row = cursor.fetchone()
    prev_correct = int(row[0]) if row else 0
    prev_total   = int(row[1]) if row else 0
    prev_passed  = bool(row[2]) if row else False

    new_correct = prev_correct + max(0, int(correct or 0))
    new_total   = prev_total + max(0, int(total or 0))
    new_pct     = round(100 * new_correct / new_total) if new_total else 0
    new_passed  = new_correct >= target

    if row:
        cursor.execute(
            "UPDATE daily_challenges SET score=%s, total=%s, percentage=%s, "
            "passed=%s, attempts=attempts+1 "
            "WHERE user_id=%s AND subject=%s AND challenge_date=%s",
            (new_correct, new_total, new_pct, new_passed, user_id, subject, today),
        )
    else:
        cursor.execute(
            "INSERT INTO daily_challenges "
            "(user_id, subject, challenge_date, score, total, percentage, passed, attempts) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 1)",
            (user_id, subject, today, new_correct, new_total, new_pct, new_passed),
        )
    conn.commit()
    return prev_passed, new_passed, new_correct, new_total



def _lazy_streak_maintenance(cursor, conn, user_id, today):
    """Read-time streak maintenance, called from GET endpoints.

    Per spec: "Freeze automatically protects the streak if exactly 1 day is missed."
    This used to require the user to qualify on the day AFTER the gap — but a real
    user just opens the app and expects the freeze to have already fired. So on
    every read we:
      1. Regen the weekly freeze (cap-1) if a new ISO week has started.
      2. Auto-fire freeze(s) to bridge any gap between last_qualified_date and
         today that fits inside the user's freeze budget. Bridged days get
         recorded as freeze_used_date so the weekly strip can render ❄️.
      3. If the gap is bigger than the budget, reset current_streak to 0.

    Conservative: only mutates when state actually changed. No-op for users with
    no streak row, or with last_qualified_date == today.
    """
    from datetime import timedelta
    cursor.execute(
        "SELECT current_streak, last_qualified_date, freezes_available, "
        "freeze_last_granted, freeze_used_date FROM streaks WHERE user_id = %s",
        (user_id,),
    )
    row = cursor.fetchone()
    if not row:
        return
    current, last_q, freezes, freeze_granted, freeze_used_date = row

    dirty = False

    # Weekly freeze policy: new ISO week tops the user up to a floor of 1 free
    # freeze on read. No stacking — already holding one means no extra grant.
    if freeze_granted is None or today.isocalendar()[:2] != freeze_granted.isocalendar()[:2]:
        freezes = max(freezes or 0, 1)
        freeze_granted = today
        dirty = True

    # 2/3. Gap handling. Only matters when there's a live streak with a real
    # last_qualified_date strictly before today.
    if last_q is not None and current > 0 and last_q < today:
        missed = (today - last_q).days - 1  # days strictly between (excl. today)
        if missed > 0:
            if missed <= freezes:
                # Auto-fire freeze(s). Record the most recent bridged day so the
                # weekly strip renders ❄️ on it. Advance last_qualified to the
                # bridged day so we don't re-fire on the next read.
                freezes -= missed
                bridged = today - timedelta(days=1)
                freeze_used_date = bridged
                last_q = bridged
                dirty = True
            else:
                # Gap too wide — freeze can't save it. Streak dies; preserve longest.
                current = 0
                dirty = True

    if dirty:
        cursor.execute(
            "UPDATE streaks SET current_streak = %s, last_qualified_date = %s, "
            "freezes_available = %s, freeze_last_granted = %s, "
            "freeze_used_date = %s WHERE user_id = %s",
            (current, last_q, freezes, freeze_granted, freeze_used_date, user_id),
        )
        conn.commit()


# Student-facing tier name + description for each O-Level band (worst -> best).
RANK_TIER_NAMES = {
    "F9": "Beginner", "E8": "Apprentice", "D7": "Advanced",
    "C6": "Scholar", "C5": "Expert", "B4": "Elite",
    "B3": "Master", "A2": "Champion", "A1": "Legend",
}
RANK_TIER_DESC = {
    "F9": "Very weak foundation. Struggles with basic concepts and lacks confidence "
          "in answering questions independently. Still building core understanding.",
    "E8": "Developing foundational knowledge across topics. Can apply basic methods "
          "but struggles with unfamiliar or multi-step questions.",
    "D7": "Above-average understanding with decent problem-solving ability. Can handle "
          "intermediate difficulty questions independently.",
    "C6": "Strong grasp of concepts and reliable performance across most topics. Makes "
          "fewer careless mistakes and thinks more analytically.",
    "C5": "Demonstrates solid conceptual mastery and good application skills. "
          "Comfortable with challenging questions and complex problem-solving.",
    "B4": "High-performing student with strong accuracy, speed, and consistency. "
          "Understands deeper patterns and advanced techniques well.",
    "B3": "Exceptional understanding and strong critical thinking ability. Performs "
          "confidently even under pressure and rarely struggles with difficult questions.",
    "A2": "Near top-tier mastery. Highly consistent, efficient, and capable of solving "
          "advanced questions with precision and confidence.",
    "A1": "Outstanding academic mastery with elite-level understanding, accuracy, and "
          "problem-solving ability. Performs at the highest level consistently.",
}
# Academic-journey icon set (sprout -> crown). One dict — swap freely.
RANK_TIER_ICONS = {
    "F9": "\U0001F331", "E8": "\U0001F530", "D7": "\U0001F4D8",
    "C6": "\U0001F393", "C5": "\U0001F9E0", "B4": "\u26A1",
    "B3": "\U0001F6E1\uFE0F", "A2": "\U0001F3C6", "A1": "\U0001F451",
}


def _band_index(band: str) -> int:
    try:
        return RANK_BANDS.index(band)
    except ValueError:
        return 0


def streak_floor_for_rank(rank_band: str) -> int:
    """Streak floor scales with rank: the % floor of the band two ranks below
    the user's current band (clamped at F9). Keeps the streak a habit signal
    that is never cruel relative to where the student actually is."""
    idx = _band_index(rank_band)
    return BAND_FLOOR[RANK_BANDS[max(0, idx - 2)]]


def _sg_today():
    """Today's date in Singapore time (the app's audience is SG)."""
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=8))).date()


def _effective_today(user_id):
    """SG 'today' for the given user.

    This used to add a per-user dev-tools day offset (the streak test panel),
    but the test tools were removed and test_day_offset is permanently 0. It
    is now a pure function — no DB round-trip — which matters because it was
    being called on nearly every endpoint and each call opened a connection.
    The user_id argument is kept so existing call sites need no change."""
    return _sg_today()


def get_user_topic_accuracy(user_id: int) -> dict:
    """Per-topic accuracy (%) from the user's quiz history. {topic: pct}."""
    import json as _json
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT questions_data FROM quiz_attempts WHERE user_id = %s",
            (user_id,),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()
    agg = {}  # topic -> [correct, total]
    for (qjson,) in rows:
        try:
            questions = _json.loads(qjson) if qjson else []
        except Exception:
            continue
        for q in questions:
            if not isinstance(q, dict):
                continue
            topic = q.get("subtopic") or "Mixed"
            agg.setdefault(topic, [0, 0])
            agg[topic][0] += int(bool(q.get("is_correct")))
            agg[topic][1] += 1
    return {t: round(100 * c / n) for t, (c, n) in agg.items() if n > 0}


@app.get("/api/daily-challenge")
def get_daily_challenge(subject: str = "Physics", authorization: str = Header(None)):
    """Today's Daily Challenge: DAILY_CHALLENGE_LEN questions, weak-topic weighted."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get("user_id")

        cache.ensure_fresh()
        pool = cache.get_filtered_questions(subject=subject)
        if not pool:
            raise HTTPException(status_code=400, detail=f"No questions found for subject {subject!r}")

        # Group the pool by topic, then draw weighted toward the user's weak topics.
        from collections import defaultdict
        by_topic = defaultdict(list)
        for q in pool:
            by_topic[q.subtopic or "Mixed"].append(q)
        for bucket in by_topic.values():
            random.shuffle(bucket)

        topic_acc = get_user_topic_accuracy(user_id)
        topic_weights = {}
        for t in by_topic:
            if t in topic_acc:
                # weaker topic -> heavier weight
                topic_weights[t] = max(5, 100 - topic_acc[t])
            else:
                topic_weights[t] = 40  # unseen topic — moderate coverage

        selected = []
        guard = 0
        while len(selected) < DAILY_CHALLENGE_LEN and guard < 500:
            guard += 1
            avail = [t for t in by_topic if by_topic[t]]
            if not avail:
                break
            weights = [topic_weights[t] for t in avail]
            t = random.choices(avail, weights=weights, k=1)[0]
            selected.append(by_topic[t].pop())
        random.shuffle(selected)

        # Deep-copy and resolve setup diagrams + option images (same as placement).
        from copy import deepcopy
        selected = [deepcopy(q) for q in selected]
        for question in selected:
            setup_uid = question.uid.rstrip("-")
            setup_info = cache.setup_info_map.get(setup_uid)
            if setup_info:
                if setup_info.get("text"):
                    question.question_text = setup_info["text"] + "\n\n" + question.question_text
                if not question.diagram_file_id and setup_info.get("file_id"):
                    question.diagram_file_id = setup_info["file_id"]
            if question.diagram_file_id:
                actual_file_id = question.diagram_file_id
                if actual_file_id:
                    question.setup_image_url = f"{PUBLIC_BASE_URL}/api/image/{actual_file_id}"
            if question.option_type == "IMAGE":
                if question.options_image_uid:
                    options_file_id = question.options_image_uid
                    if options_file_id:
                        question.image_url = f"{PUBLIC_BASE_URL}/api/image/{options_file_id}"
            else:
                if question.setup_image_url:
                    question.image_url = question.setup_image_url

        # Has the user already cleared today's challenge for this subject?
        today = _effective_today(user_id)
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT passed, attempts FROM daily_challenges "
                "WHERE user_id = %s AND subject = %s AND challenge_date = %s",
                (user_id, subject, today),
            )
            row = cursor.fetchone()
            cursor.execute(
                "SELECT rank_band, rank_score FROM user_subject_ranks "
                "WHERE user_id = %s AND subject = %s",
                (user_id, subject),
            )
            _frow = cursor.fetchone()
        finally:
            cursor.close()
            conn.close()
        already_passed = bool(row[0]) if row else False
        attempts_today = row[1] if row else 0
        streak_floor = streak_floor_for_rank(_frow[0]) if _frow else STREAK_FLOOR
        rank = None
        if _frow:
            rank = {
                "rank_band": _frow[0],
                "rank_score": _frow[1],
                "tier_name": RANK_TIER_NAMES.get(_frow[0], ""),
                "tier_icon": RANK_TIER_ICONS.get(_frow[0], ""),
            }

        # Today's cumulative correct progress (drives Home's "X / 10 today" card)
        # We re-read because the row above only returned (passed, attempts).
        try:
            conn2 = get_db_connection()
            cursor2 = conn2.cursor()
            cursor2.execute(
                "SELECT score, total FROM daily_challenges "
                "WHERE user_id = %s AND subject = %s AND challenge_date = %s",
                (user_id, subject, today),
            )
            _drow = cursor2.fetchone()
            today_correct = int(_drow[0]) if _drow else 0
            today_total   = int(_drow[1]) if _drow else 0
            cursor2.close(); conn2.close()
        except Exception:
            today_correct = 0
            today_total = 0

        return {
            "subject": subject,
            "count": len(selected),
            "questions": selected,
            "already_passed_today": already_passed,
            "attempts_today": attempts_today,
            "streak_floor": streak_floor,
            "rank": rank,
            "daily_progress": {
                "today_correct": today_correct,
                "today_total": today_total,
                "target": DAILY_CORRECT_TARGET,
                "passed_today": already_passed,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"\u274c Error in /api/daily-challenge: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class DailyChallengeSubmitRequest(BaseModel):
    subject: str = "Physics"
    score: int
    total: int


@app.post("/api/daily-challenge/submit")
def submit_daily_challenge(request: DailyChallengeSubmitRequest, authorization: str = Header(None)):
    """Score a Daily Challenge: record it, update the streak, move rank if warranted."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get("user_id")

        if request.total <= 0:
            raise HTTPException(status_code=400, detail="total must be greater than 0")
        score = max(0, min(request.score, request.total))
        percentage = round(100 * score / request.total)
        today = _effective_today(user_id)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Streak floor scales with rank — the band two ranks below the user's.
            cursor.execute(
                "SELECT rank_band FROM user_subject_ranks "
                "WHERE user_id = %s AND subject = %s",
                (user_id, request.subject),
            )
            _frow = cursor.fetchone()
            streak_floor = streak_floor_for_rank(_frow[0]) if _frow else STREAK_FLOOR
            passed = percentage >= streak_floor

            # 1. Upsert today's daily_challenges row (one per user+subject+day).
            cursor.execute(
                """
                INSERT INTO daily_challenges
                    (user_id, subject, challenge_date, score, total, percentage, passed, attempts)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    score = VALUES(score), total = VALUES(total),
                    percentage = VALUES(percentage),
                    passed = GREATEST(passed, VALUES(passed)),
                    attempts = attempts + 1
                """,
                (user_id, request.subject, today, score, request.total, percentage, passed),
            )
            conn.commit()

            # 2. Streak.
            cursor.execute(
                "SELECT current_streak, longest_streak, last_qualified_date, "
                "freezes_available, freeze_last_granted FROM streaks WHERE user_id = %s",
                (user_id,),
            )
            srow = cursor.fetchone()
            if not srow:
                cursor.execute(
                    "INSERT INTO streaks (user_id, current_streak, longest_streak, "
                    "freezes_available, freeze_last_granted) VALUES (%s, 0, 0, 1, %s)",
                    (user_id, today),
                )
                conn.commit()
                current_streak, longest_streak, last_qualified = 0, 0, None
                freezes, freeze_granted = 1, today
            else:
                current_streak, longest_streak, last_qualified, freezes, freeze_granted = srow

            # Freeze regeneration: 1 per 7 days, capped at 1.
            if freeze_granted is None:
                freeze_granted = today
            if (today - freeze_granted).days >= 7 and freezes < 1:
                freezes = 1
                freeze_granted = today

            freeze_used = False
            if passed:
                if last_qualified == today:
                    pass  # already earned today (retry after a pass) — no change
                else:
                    if last_qualified is None:
                        current_streak = 1
                    else:
                        missed = (today - last_qualified).days - 1
                        if missed <= 0:
                            current_streak += 1
                        elif missed <= freezes:
                            freezes -= missed
                            freeze_used = True
                            current_streak += 1
                        else:
                            current_streak = 1
                    longest_streak = max(longest_streak, current_streak)
                    last_qualified = today
                cursor.execute(
                    "UPDATE streaks SET current_streak = %s, longest_streak = %s, "
                    "last_qualified_date = %s, freezes_available = %s, "
                    "freeze_last_granted = %s WHERE user_id = %s",
                    (current_streak, longest_streak, last_qualified, freezes, freeze_granted, user_id),
                )
                conn.commit()
            else:
                cursor.execute(
                    "UPDATE streaks SET freezes_available = %s, freeze_last_granted = %s "
                    "WHERE user_id = %s",
                    (freezes, freeze_granted, user_id),
                )
                conn.commit()

            # 3. Rank movement — rolling window of Daily Challenges since the last
            #    rank change (placement or a previous movement reset the window).
            #    Re-wired 2026-05-14: Daily Challenge drives streak AND rank again.
            #    Trade-off knowingly accepted: one bad day affects both.
            rank_change = {"changed": False}
            cursor.execute(
                "SELECT rank_band, rank_score, updated_at FROM user_subject_ranks "
                "WHERE user_id = %s AND subject = %s",
                (user_id, request.subject),
            )
            rrow = cursor.fetchone()
            if rrow:
                cur_band, cur_score, rank_updated_at = rrow
                cursor.execute(
                    "SELECT percentage FROM daily_challenges "
                    "WHERE user_id = %s AND subject = %s AND updated_at > %s "
                    "ORDER BY challenge_date DESC LIMIT 5",
                    (user_id, request.subject, rank_updated_at),
                )
                window = [r[0] for r in cursor.fetchall()]
                if len(window) >= 5:
                    idx = _band_index(cur_band)
                    new_band = None
                    # Promote: 3 of last 5 at/above the next band's floor.
                    if idx < len(RANK_BANDS) - 1:
                        next_up = RANK_BANDS[idx + 1]
                        if sum(1 for p in window if p >= BAND_FLOOR[next_up]) >= 3:
                            new_band = next_up
                    # Demote: 4 of last 5 below the current band's floor.
                    if new_band is None and idx > 0:
                        if sum(1 for p in window if p < BAND_FLOOR[cur_band]) >= 4:
                            new_band = RANK_BANDS[idx - 1]
                    if new_band:
                        new_score = round(sum(window) / len(window))
                        cursor.execute(
                            "UPDATE user_subject_ranks SET rank_band = %s, rank_score = %s "
                            "WHERE user_id = %s AND subject = %s",
                            (new_band, new_score, user_id, request.subject),
                        )
                        cursor.execute(
                            "INSERT INTO rank_history (user_id, subject, rank_band, rank_score) "
                            "VALUES (%s, %s, %s, %s)",
                            (user_id, request.subject, new_band, new_score),
                        )
                        conn.commit()
                        rank_change = {
                            "changed": True,
                            "direction": "up" if _band_index(new_band) > idx else "down",
                            "old_band": cur_band,
                            "new_band": new_band,
                        }
        finally:
            cursor.close()
            conn.close()

        return {
            "passed": passed,
            "score": score,
            "total": request.total,
            "percentage": percentage,
            "streak": {
                "current": current_streak,
                "longest": longest_streak,
                "freeze_used": freeze_used,
                "freezes_available": freezes,
                "earned_today": bool(passed),
            },
            "rank": rank_change,
            "streak_floor": streak_floor,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"\u274c Error in /api/daily-challenge/submit: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/streak")
def get_streak(authorization: str = Header(None)):
    """Current streak status. Lazily expires a dead streak so the display is honest."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get("user_id")
        today = _effective_today(user_id)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Lazy maintenance: regen weekly freeze + auto-fire freeze for any
            # single missed day. Mutates DB so the subsequent SELECT is truth.
            _lazy_streak_maintenance(cursor, conn, user_id, today)

            cursor.execute(
                "SELECT current_streak, longest_streak, last_qualified_date, "
                "freezes_available, freeze_last_granted, freeze_used_date "
                "FROM streaks WHERE user_id = %s",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return {
                    "current_streak": 0, "longest_streak": 0, "freezes_available": 1,
                    "did_today": False, "last_qualified_date": None,
                    "freeze_used_date": None, "effective_today": str(today),
                }
            current, longest, last_q, freezes, freeze_granted, freeze_used_date = row
            did_today = (last_q == today)

            return {
                "current_streak": current,
                "longest_streak": longest,
                "freezes_available": freezes,
                "did_today": did_today,
                "last_qualified_date": str(last_q) if last_q else None,
                "freeze_used_date": str(freeze_used_date) if freeze_used_date else None,
                "effective_today": str(today),
            }
        finally:
            cursor.close()
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        print(f"\u274c Error in /api/streak: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/streak/week")
def get_streak_week(authorization: str = Header(None)):
    """This week's per-day streak status (Mon -> Sun in SG time).
    Returns 7 day cells each with status: completed / freeze_used / today / missed / upcoming.
    Drives the Home page's weekly strip."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get("user_id")

        from datetime import timedelta
        today = _effective_today(user_id)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Same lazy maintenance as /api/streak so the weekly strip stays in
            # sync with the counter (e.g. auto-fired freeze shows as ❄️).
            _lazy_streak_maintenance(cursor, conn, user_id, today)

            # Fetch streak info FIRST — we anchor the week on max(today, last_q)
            # so test-mode credits that push last_q into a future week still get
            # rendered. Real users always have last_q <= today, so the anchor
            # collapses to today and production behaviour is unchanged.
            cursor.execute(
                "SELECT last_qualified_date, freeze_used_date FROM streaks WHERE user_id = %s",
                (user_id,),
            )
            srow = cursor.fetchone()
            last_q = srow[0] if srow and srow[0] else None
            freeze_used_date = srow[1] if srow and srow[1] else None

            anchor = today if last_q is None else max(today, last_q)
            monday = anchor - timedelta(days=anchor.weekday())
            sunday = monday + timedelta(days=6)
            week_dates = [monday + timedelta(days=i) for i in range(7)]

            cursor.execute(
                "SELECT challenge_date, passed FROM daily_challenges "
                "WHERE user_id = %s AND challenge_date BETWEEN %s AND %s",
                (user_id, monday, sunday),
            )
            passed_map = {}
            for row in cursor.fetchall():
                d, p = row[0], bool(row[1])
                passed_map[d] = passed_map.get(d, False) or p
        finally:
            cursor.close()
            conn.close()

        weekday_names = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
        days = []
        for d in week_dates:
            if passed_map.get(d):
                status = 'completed'
            elif freeze_used_date and d == freeze_used_date:
                status = 'freeze_used'
            else:
                # No data for this day. A day is "upcoming" ONLY if it sits beyond
                # both real today AND the streak's progress (last_qualified).
                # Days the streak has already passed through but have no row are
                # 'missed' (e.g. test-mode skipped Thu while crediting Fri).
                is_truly_future = d > today and (last_q is None or d > last_q)
                if is_truly_future:
                    status = 'upcoming'
                elif d == today:
                    status = 'today'   # in progress (today not passed yet)
                else:
                    status = 'missed'
            days.append({
                'date': str(d),
                'weekday': weekday_names[d.weekday()],
                'is_today': d == today,
                'status': status,
            })

        return {
            'week_start': str(monday),
            'week_end':   str(sunday),
            'today':      str(today),
            'days':       days,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"\u274c Error in /api/streak/week: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/api/leaderboard")
def get_leaderboard(
    authorization: str = Header(None),
    period: str = "weekly",
    limit: int  = 50,
):
    """Global leaderboard. `period` is one of:
      - 'daily'   -> XP earned today (SG)
      - 'weekly'  -> XP earned over the current ISO week (Mon-Sun)
      - 'alltime' -> users.xp (lifetime total)

    Returns the top `limit` users plus the current user appended at the end
    (with their actual rank index) when they fall outside the top slice.
    """
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        me_id = int(payload.get("user_id"))

        period = (period or "weekly").lower()
        if period not in {"daily", "weekly", "alltime"}:
            raise HTTPException(status_code=400, detail="period must be daily|weekly|alltime")

        from datetime import timedelta
        today  = _effective_today(me_id)
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if period == "alltime":
                # Skinny query — avatar_url is a LONGTEXT data-URL (up to
                # 1.5 MB per user); selecting it for EVERY user shipped the
                # whole student body's avatar images on each leaderboard view.
                # Avatars are attached below, only for the returned slice.
                cursor.execute("""
                    SELECT id, name, equipped, COALESCE(xp, 0) AS score
                    FROM users
                    WHERE name IS NOT NULL AND name <> ''
                    ORDER BY score DESC, id ASC
                """)
            elif period == "daily":
                cursor.execute("""
                    SELECT u.id, u.name, u.equipped,
                           COALESCE(SUM(dc.xp), 0) AS score
                    FROM users u
                    LEFT JOIN daily_challenges dc
                      ON dc.user_id = u.id AND dc.challenge_date = %s
                    WHERE u.name IS NOT NULL AND u.name <> ''
                    GROUP BY u.id, u.name, u.equipped
                    ORDER BY score DESC, u.id ASC
                """, (today,))
            else:  # weekly
                cursor.execute("""
                    SELECT u.id, u.name, u.equipped,
                           COALESCE(SUM(dc.xp), 0) AS score
                    FROM users u
                    LEFT JOIN daily_challenges dc
                      ON dc.user_id = u.id
                     AND dc.challenge_date BETWEEN %s AND %s
                    WHERE u.name IS NOT NULL AND u.name <> ''
                    GROUP BY u.id, u.name, u.equipped
                    ORDER BY score DESC, u.id ASC
                """, (monday, sunday))

            rows = cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

        # Build the ranked list. Rank ties share the higher position (dense rank
        # would be confusing on a podium — competition rank is more familiar).
        full = []
        prev_score = None
        prev_rank  = 0
        for idx, (uid, name, equipped_raw, score) in enumerate(rows, start=1):
            score = int(score or 0)
            if prev_score is None or score != prev_score:
                rank = idx
                prev_rank  = idx
                prev_score = score
            else:
                rank = prev_rank
            # Level is only meaningful when score IS the all-time XP total.
            level = compute_level(score) if period == "alltime" else None
            full.append({
                "user_id":    int(uid),
                "name":       name,
                "avatar_url": None,  # attached below, only for the top slice
                "equipped":   _parse_equipped(equipped_raw),
                "score":      score,
                "rank":       rank,
                "is_me":      int(uid) == me_id,
                "level":      level,
            })

        top = full[: max(1, int(limit))]
        # Always include me. If I'm not in the top slice, append my row.
        if not any(p["is_me"] for p in top):
            me = next((p for p in full if p["is_me"]), None)
            if me:
                top.append(me)

        # Attach avatars for just the rows we actually return (~20-50),
        # not the whole table.
        ids = [p["user_id"] for p in top]
        if ids:
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                marks = ",".join(["%s"] * len(ids))
                cursor.execute(
                    f"SELECT id, avatar_url FROM users WHERE id IN ({marks})",
                    ids,
                )
                amap = {int(r[0]): r[1] for r in cursor.fetchall()}
            finally:
                cursor.close()
                conn.close()
            for p in top:
                p["avatar_url"] = amap.get(p["user_id"])

        return {
            "period":     period,
            "today":      str(today),
            "week_start": str(monday),
            "week_end":   str(sunday),
            "total_users": len(full),
            "entries":    top,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in /api/leaderboard: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ---------------------------------------------------------------------------
# StarQuest §05 — Rewards Shop. Static catalogue, mirrors newFrontend/index.html.
# Costs are in gems. Physical items are mailed monthly to school; digital
# items (avatars) unlock instantly.
# ---------------------------------------------------------------------------
SHOP_CATALOGUE = [
    # Wearables catalogue — Mr Potato Head style. Each item has a `slot`
    # and a `rarity` tier. Rarities drive both pricing and the colored
    # badge in the UI. Pricing benchmarks (earn rate ≈ 25 gems / perfect
    # 10-Q quiz):
    #   common    → 150-250   (~6-10 perfect quizzes)
    #   rare      → 400-600   (~16-24 perfect quizzes)
    #   epic      → 900-1200  (~36-48 perfect quizzes)
    #   legendary → 2200-3000 (~88-120 perfect quizzes)
    # ── Hats ──────────────────────────────────────────────────────
    {"id": "hat_grad",     "slot": "hat",       "rarity": "common",    "name": "Graduation Cap", "cost": 150,  "emoji": "🎓", "desc": "For the academically inclined."},
    {"id": "hat_top",      "slot": "hat",       "rarity": "rare",      "name": "Top Hat",        "cost": 450,  "emoji": "🎩", "desc": "Classy. Formal. Iconic."},
    {"id": "hat_cowboy",   "slot": "hat",       "rarity": "rare",      "name": "Cowboy Hat",     "cost": 550,  "emoji": "🤠", "desc": "Yeehaw. Saddle up."},
    {"id": "hat_crown",    "slot": "hat",       "rarity": "legendary", "name": "Royal Crown",    "cost": 2500, "emoji": "👑", "desc": "Heavy is the head that wears it."},
    {"id": "hat_helmet",   "slot": "hat",       "rarity": "rare",      "name": "Military Helmet","cost": 500,  "emoji": "🪖", "desc": "Battle-tested headgear."},
    {"id": "hat_wizard",   "slot": "hat",       "rarity": "epic",      "name": "Wizard Hat",     "cost": 1100, "emoji": "🧙", "desc": "Channel arcane physics knowledge."},

    # ── Glasses ───────────────────────────────────────────────────
    {"id": "glasses_round","slot": "glasses",   "rarity": "common",    "name": "Round Glasses",  "cost": 150,  "emoji": "👓", "desc": "Studious and sharp."},
    {"id": "glasses_sun",  "slot": "glasses",   "rarity": "rare",      "name": "Sunglasses",     "cost": 400,  "emoji": "🕶️", "desc": "Too cool for school."},
    {"id": "glasses_mono", "slot": "glasses",   "rarity": "epic",      "name": "Monocle",        "cost": 1000, "emoji": "🧐", "desc": "Quite distinguished, I dare say."},
    {"id": "glasses_vr",   "slot": "glasses",   "rarity": "rare",      "name": "VR Goggles",     "cost": 550,  "emoji": "🥽", "desc": "Step into the metaverse."},

    # ── Accessories (corner badge) ───────────────────────────────
    {"id": "acc_bow",      "slot": "accessory", "rarity": "common",    "name": "Pink Bow",       "cost": 200,  "emoji": "🎀", "desc": "Cute and pretty."},
    {"id": "acc_star",     "slot": "accessory", "rarity": "rare",      "name": "Star Pin",       "cost": 450,  "emoji": "⭐", "desc": "A little sparkle."},
    {"id": "acc_fire",     "slot": "accessory", "rarity": "epic",      "name": "Fire Badge",     "cost": 1100, "emoji": "🔥", "desc": "On fire. Literally."},
    {"id": "acc_medal",    "slot": "accessory", "rarity": "rare",      "name": "Gold Medal",     "cost": 500,  "emoji": "🎖️", "desc": "Earned, not given."},
    {"id": "acc_trophy",   "slot": "accessory", "rarity": "epic",      "name": "Trophy",         "cost": 1100, "emoji": "🏆", "desc": "Champion of the cosmos."},
    {"id": "acc_diamond",  "slot": "accessory", "rarity": "legendary", "name": "Diamond",        "cost": 2800, "emoji": "💎", "desc": "Forged under cosmic pressure."},

    # ── Frames (CSS rings, no emoji rendered on avatar) ──────────
    {"id": "frame_gold",   "slot": "frame",     "rarity": "epic",      "name": "Gold Frame",     "cost": 1200, "emoji": "🟡", "desc": "Lustrous gold ring.",  "value": "gold"},
    {"id": "frame_rainbow","slot": "frame",     "rarity": "legendary", "name": "Rainbow Frame",  "cost": 2200, "emoji": "🌈", "desc": "Prismatic ring.",      "value": "rainbow"},
    {"id": "frame_fire",   "slot": "frame",     "rarity": "legendary", "name": "Flame Frame",    "cost": 3000, "emoji": "🔥", "desc": "Animated fire ring.",  "value": "fire"},
    {"id": "frame_galaxy", "slot": "frame",     "rarity": "legendary", "name": "Galaxy Frame",   "cost": 2600, "emoji": "🌌", "desc": "Swirling deep-space ring.",   "value": "galaxy"},

    # ── Hands (arms stick out both sides of the avatar) ──────────
    {"id": "hands_wave",   "slot": "hands",     "rarity": "common",    "name": "Waving Hands",   "cost": 200,  "emoji": "👋", "desc": "Friendly hello on both sides."},
    {"id": "hands_peace",  "slot": "hands",     "rarity": "common",    "name": "Peace Hands",    "cost": 250,  "emoji": "✌️", "desc": "Twin peace signs."},
    {"id": "hands_glove",  "slot": "hands",     "rarity": "rare",      "name": "Winter Gloves",  "cost": 500,  "emoji": "🧤", "desc": "Cosy mittens."},
    {"id": "hands_fist",   "slot": "hands",     "rarity": "rare",      "name": "Power Fists",    "cost": 600,  "emoji": "✊", "desc": "Hold your ground."},
    {"id": "hands_muscle", "slot": "hands",     "rarity": "epic",      "name": "Flex Arms",      "cost": 1100, "emoji": "💪", "desc": "Show those gains."},
    {"id": "hands_clap",   "slot": "hands",     "rarity": "rare",      "name": "Clapping Hands", "cost": 450,  "emoji": "👏", "desc": "Round of applause."},
    {"id": "hands_rock",   "slot": "hands",     "rarity": "epic",      "name": "Rock On!",       "cost": 1000, "emoji": "🤘", "desc": "Stay metal."},
    {"id": "hands_magic",  "slot": "hands",     "rarity": "legendary", "name": "Magic Hands",    "cost": 2500, "emoji": "✨", "desc": "Sparkle on contact."},

    # ── Legs (two feet stick out from the bottom of the avatar) ──
    {"id": "legs_sneaker", "slot": "legs",      "rarity": "common",    "name": "Sneakers",       "cost": 200,  "emoji": "👟", "desc": "Light on your feet."},
    {"id": "legs_boot",    "slot": "legs",      "rarity": "rare",      "name": "Hiking Boots",   "cost": 500,  "emoji": "🥾", "desc": "Built for the climb."},
    {"id": "legs_dress",   "slot": "legs",      "rarity": "rare",      "name": "Dress Shoes",    "cost": 600,  "emoji": "👞", "desc": "Sharp and polished."},
    {"id": "legs_cowboy",  "slot": "legs",      "rarity": "epic",      "name": "Cowboy Boots",   "cost": 1000, "emoji": "👢", "desc": "Saddle up partner."},
    {"id": "legs_ballet",  "slot": "legs",      "rarity": "epic",      "name": "Ballet Slippers","cost": 1200, "emoji": "🩰", "desc": "On your toes."},
    {"id": "legs_skate",   "slot": "legs",      "rarity": "epic",      "name": "Skateboard",     "cost": 1100, "emoji": "🛹", "desc": "Roll into class."},
    {"id": "legs_rocket",  "slot": "legs",      "rarity": "legendary", "name": "Rocket Boots",   "cost": 2800, "emoji": "🚀", "desc": "Blast off — physics demands it."},

    # ── Skin tones (the base monkey's fur colour) ─────────────────────
    # slot "skin" is the BASE layer — free to switch between, like Duolingo
    # letting you pick your character's look. All cost 0 (equippable without
    # an ownership row). Paid wearables above layer on top of any skin tone.
    {"id": "skin_default",  "slot": "skin", "rarity": "common", "name": "Classic Brown", "cost": 0, "emoji": "🐵", "desc": "The original Ooka."},
    {"id": "skin_tan",      "slot": "skin", "rarity": "common", "name": "Tan",           "cost": 0, "emoji": "🐵", "desc": "A lighter, warmer coat."},
    {"id": "skin_espresso", "slot": "skin", "rarity": "common", "name": "Espresso",      "cost": 0, "emoji": "🐵", "desc": "Deep, rich dark brown."},
    {"id": "skin_grey",     "slot": "skin", "rarity": "common", "name": "Silver",        "cost": 0, "emoji": "🐵", "desc": "Cool silver-grey fur."},
    {"id": "skin_golden",   "slot": "skin", "rarity": "common", "name": "Golden",        "cost": 0, "emoji": "🐵", "desc": "Sunny golden monkey."},
    {"id": "skin_cream",    "slot": "skin", "rarity": "common", "name": "Cream",         "cost": 0, "emoji": "🐵", "desc": "Soft pale cream."},

    # ── Outfits (full-body clothing worn over any skin tone) ───────────
    # slot "outfit" renders as a PNG layered on the monkey's torso/arms.
    # Free for now (cost 0) so it shows in the currently free-only shop.
    {"id": "hoodie_navy",   "slot": "outfit", "rarity": "rare", "name": "Ooka Hoodie", "cost": 0, "emoji": "🧥", "desc": "The classic navy Ooka hoodie."},

    # ── Wearables (metadata-placed SVGs; free so they show in the shop) ──
    {"id": "cap_ooka",      "slot": "hat",       "rarity": "common", "name": "Ooka Cap",    "cost": 0, "emoji": "🧢", "desc": "Navy cap with the gold O."},
    {"id": "shades",        "slot": "glasses",   "rarity": "common", "name": "Cool Shades", "cost": 0, "emoji": "🕶️", "desc": "Too cool for school."},
    {"id": "crown_gold",    "slot": "hat",       "rarity": "rare",   "name": "Gold Crown",  "cost": 0, "emoji": "👑", "desc": "Rule the leaderboard."},
    {"id": "scarf_red",     "slot": "accessory", "rarity": "common", "name": "Cozy Scarf",  "cost": 0, "emoji": "🧣", "desc": "Warm and stylish."},
    {"id": "beanie_teal",   "slot": "hat",       "rarity": "common", "name": "Cozy Beanie", "cost": 0, "emoji": "🧶", "desc": "Knit and toasty."},
    {"id": "glasses_round", "slot": "glasses",   "rarity": "common", "name": "Round Specs",  "cost": 0, "emoji": "🤓", "desc": "Smart and studious."},
    {"id": "bowtie_red",    "slot": "accessory", "rarity": "common", "name": "Red Bowtie",   "cost": 0, "emoji": "🎀", "desc": "Dapper and sharp."},
    {"id": "cape_red",      "slot": "backItem",  "rarity": "rare",   "name": "Hero Cape",    "cost": 0, "emoji": "🦸", "desc": "Save the leaderboard."},
    {"id": "wings_angel",   "slot": "backItem",  "name": "Angel Wings", "cost": 10, "released": True, "emoji": "😇", "desc": "Ascend the ranks on feathered wings."},
]
SHOP_BY_ID = {item["id"]: item for item in SHOP_CATALOGUE}

# Number of days a new account must wait before redeeming any shop item.
# Drives both the /api/shop unlock fields and the /api/shop/redeem gate.
# Set deliberately high enough that students feel they earned the right to
# spend — anti-impulse, builds anticipation, prevents "buy and quit" cohorts.
MIN_ACCOUNT_AGE_DAYS = 7

def _account_age_days(user_id):
    """Return (age_days, unlocked, days_until_unlock) for the user.

    Reads users.created_at and compares against MIN_ACCOUNT_AGE_DAYS.
    Defaults to LOCKED (0, False, MIN_ACCOUNT_AGE_DAYS) when created_at
    is missing — paranoid default; better to make a real user wait one
    extra day than to let a malformed row bypass the gate."""
    from datetime import datetime
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT created_at FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()
    if not row or row[0] is None:
        return (0, False, MIN_ACCOUNT_AGE_DAYS)
    age = max(0, int((datetime.now() - row[0]).days))
    unlocked = age >= MIN_ACCOUNT_AGE_DAYS
    return (age, unlocked, max(0, MIN_ACCOUNT_AGE_DAYS - age))



import json as _json

# "skin" is the base-character slot (the monkey's fur tone); "outfit" is a
# full-body clothing layer (PNG); the rest are overlay wearables on top.
_WEARABLE_SLOTS = {"skin", "outfit", "hat", "glasses", "accessory", "frame", "hands", "legs", "backItem"}


def _parse_equipped(raw):
    """Normalise the equipped JSON column into a dict keyed by every slot."""
    out = {slot: None for slot in _WEARABLE_SLOTS}
    if not raw:
        return out
    try:
        data = raw if isinstance(raw, dict) else _json.loads(raw)
    except Exception:
        return out
    for slot in _WEARABLE_SLOTS:
        v = data.get(slot)
        if isinstance(v, str) and v.strip():
            out[slot] = v
    return out


class ShopEquipRequest(BaseModel):
    reward_id: Optional[str] = None  # None / empty = unequip
    slot: Optional[str] = None       # Inferred from catalogue when reward_id present


@app.post("/api/shop/equip")
def equip_reward(request: ShopEquipRequest, authorization: str = Header(None)):
    """Equip / unequip a wearable. Two modes:
       • reward_id set → put in its slot (must own)
       • reward_id empty + slot set → clear that slot
    """
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get("user_id")

        target_slot = request.slot
        if request.reward_id:
            item = SHOP_BY_ID.get(request.reward_id)
            if not item:
                raise HTTPException(status_code=404, detail=f"Unknown item: {request.reward_id}")
            target_slot = item.get("slot")
        if target_slot not in _WEARABLE_SLOTS:
            raise HTTPException(status_code=400, detail=f"Unknown slot: {target_slot}")

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if request.reward_id:
                # Free items (cost 0 — e.g. the default Classic Ooka avatar)
                # are equippable by everyone without an ownership row.
                item = SHOP_BY_ID.get(request.reward_id)
                if item and int(item.get("cost", 0)) > 0:
                    cursor.execute(
                        "SELECT 1 FROM user_rewards WHERE user_id = %s AND reward_id = %s",
                        (user_id, request.reward_id),
                    )
                    if not cursor.fetchone():
                        raise HTTPException(status_code=400, detail="You do not own this item")

            cursor.execute("SELECT equipped FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            equipped = _parse_equipped(row[0] if row else None)
            equipped[target_slot] = request.reward_id or None
            cursor.execute(
                "UPDATE users SET equipped = %s WHERE id = %s",
                (_json.dumps(equipped), user_id),
            )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

        return {"success": True, "equipped": equipped}
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in /api/shop/equip: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/shop")
def get_shop(authorization: str = Header(None)):
    """Return the shop catalogue + the user's gem balance + their owned reward IDs."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get("user_id")

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT gems FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            gems = int(row[0]) if row and row[0] is not None else 0

            cursor.execute(
                "SELECT reward_id FROM user_rewards WHERE user_id = %s ORDER BY redeemed_at DESC",
                (user_id,),
            )
            owned = [r[0] for r in cursor.fetchall()]

            cursor.execute("SELECT equipped FROM users WHERE id = %s", (user_id,))
            erow = cursor.fetchone()
            equipped = _parse_equipped(erow[0] if erow else None)
        finally:
            cursor.close()
            conn.close()

        age_days, shop_unlocked, days_until_unlock = _account_age_days(user_id)
        # Surface free items (skin tones) PLUS any paid item explicitly
        # marked "released": True. Unreleased paid items stay defined in
        # SHOP_CATALOGUE (so equipped ones still render) but are hidden until
        # flagged. To open the whole paid catalogue, drop this filter.
        visible_catalogue = [it for it in SHOP_CATALOGUE
                             if int(it.get("cost", 0)) == 0 or it.get("released")]
        return {
            "gems":                 gems,
            "owned":                owned,
            "equipped":             equipped,
            "catalogue":            visible_catalogue,
            "account_age_days":     age_days,
            "shop_unlocked":        shop_unlocked,
            "days_until_unlock":    days_until_unlock,
            "min_account_age_days": MIN_ACCOUNT_AGE_DAYS,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in /api/shop: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ShopRedeemRequest(BaseModel):
    reward_id: str


@app.post("/api/shop/redeem")
def redeem_reward(request: ShopRedeemRequest, authorization: str = Header(None)):
    """Spend gems to redeem one catalogue item. Idempotent-ish: UNIQUE constraint
    blocks double-redemption of the same item (returns 400 with a clear message)."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get("user_id")

        # Account-age gate: brand-new users can't redeem until they've
        # played for MIN_ACCOUNT_AGE_DAYS. Mirrors /api/shop's shop_unlocked
        # field so the UI and backend never disagree.
        _, _unlocked, _days_left = _account_age_days(user_id)
        if not _unlocked:
            raise HTTPException(
                status_code=403,
                detail=f"Shop unlocks in {_days_left} day{'' if _days_left == 1 else 's'}. Keep practicing!",
            )

        item = SHOP_BY_ID.get(request.reward_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"Unknown reward: {request.reward_id}")
        cost = int(item["cost"])

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Check balance + ownership inside the same transaction.
            cursor.execute("SELECT gems FROM users WHERE id = %s FOR UPDATE", (user_id,))
            row = cursor.fetchone()
            gems_have = int(row[0]) if row and row[0] is not None else 0
            if gems_have < cost:
                raise HTTPException(
                    status_code=400,
                    detail=f"Need {cost} gems, have {gems_have}",
                )

            cursor.execute(
                "SELECT 1 FROM user_rewards WHERE user_id = %s AND reward_id = %s",
                (user_id, item["id"]),
            )
            if cursor.fetchone():
                raise HTTPException(status_code=400, detail="Already owned")

            cursor.execute(
                "UPDATE users SET gems = gems - %s WHERE id = %s",
                (cost, user_id),
            )
            cursor.execute(
                "INSERT INTO user_rewards (user_id, reward_id, cost) VALUES (%s, %s, %s)",
                (user_id, item["id"], cost),
            )
            conn.commit()

            cursor.execute("SELECT gems FROM users WHERE id = %s", (user_id,))
            gems_after = int(cursor.fetchone()[0])
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

        return {
            "success":    True,
            "reward":     item,
            "gems_spent": cost,
            "gems_total": gems_after,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in /api/shop/redeem: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.on_event("startup")
async def startup_event():
    """Load questions and pre-cache files on startup"""
    try:
        print("\U0001f680 Starting up...")
        print("\U0001f4be Initializing database...")
        init_database()
        # Drive scan moved OFF the startup path — it recursively walks 4
        # drives (one API call per folder) and was adding tens of seconds
        # before the first request could be served. serve_image self-heals
        # via file_map lookups + Drive name-search while the scan finishes.
        print("\U0001f4c1 Pre-loading file map in background thread...")
        threading.Thread(target=cache.load_file_map, daemon=True, name="drive-scan").start()
        print("\U0001f4cb Loading questions...")
        cache.load_questions()
        print(f"\U0001f4ca Available subtopics: {cache.get_unique_subtopics()}")
        print(f"\U0001f4ca Available difficulties: {cache.get_unique_difficulties()}")
        stats = get_category_statistics()
        print("\n\U0001f4ca Question Categories:")
        for qtype, data in stats.items():
            print(f"  {qtype}: {data['count']} questions ({data['percentage']}%)")
        print("\n✅ Startup complete!")
    except Exception as e:
        print(f"❌ Failed during startup: {e}")


# ============================================================================
# TEACHER DASHBOARD
# ============================================================================
# Read-only overview for accounts flagged with is_teacher. Single endpoint so
# the dashboard loads with one round-trip. No write paths; teachers cannot
# touch student records. Promotion happens manually in the DB
# (UPDATE users SET is_teacher = TRUE WHERE email = ...).

@app.get("/api/teacher/overview")
def get_teacher_overview(
    authorization: str = Header(None),
    quiz_type: Optional[str] = None,
):
    """Teacher dashboard payload — one round-trip returns:
      - week_at_a_glance: totals across the student body for the last 7 days.
      - weakest_topics: subtopics where the class collectively struggled,
        ranked worst-first, with the names of the students who scored <60%.
      - inactive_students: students silent 5+ days or never active.

    `quiz_type` filters every stat to one slice of the quiz_attempts table:
      - omitted / 'all' -> Daily Challenge + Practice (everything)
      - 'daily'         -> only daily-challenge attempts
      - 'practice'      -> only practice attempts
    Any other value falls back to 'all'.

    Requires the is_teacher JWT claim. 403 otherwise."""
    require_teacher(authorization)

    # Whitelist the value before splicing it into SQL — only two literal
    # strings are accepted, so the f-string substitution below is safe.
    qt = (quiz_type or "").strip().lower()
    if qt not in ("daily", "practice"):
        qt = "all"
    qt_clause = f" AND qa.quiz_type = '{qt}'" if qt in ("daily", "practice") else ""

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1) Weakest topics in the last 7 days. A topic qualifies if the class
        # average is under 70 OR at least two students scored under 60. Sort by
        # the share of students struggling first, then by raw average.
        cursor.execute(f"""
            SELECT
              qa.subtopic,
              COUNT(DISTINCT qa.user_id) AS students_attempted,
              COUNT(qa.id) AS attempts,
              ROUND(AVG(qa.percentage), 1) AS avg_pct,
              COUNT(DISTINCT CASE WHEN qa.percentage < 60 THEN qa.user_id END) AS struggling_count,
              GROUP_CONCAT(DISTINCT
                  CASE WHEN qa.percentage < 60 THEN qa.user_id END
              ) AS struggling_ids
            FROM quiz_attempts qa
            JOIN users u ON u.id = qa.user_id
            WHERE u.is_teacher = FALSE
              AND qa.attempted_at >= NOW() - INTERVAL 7 DAY
              AND qa.subtopic IS NOT NULL AND qa.subtopic <> ''
              AND qa.percentage IS NOT NULL
              {qt_clause}
            GROUP BY qa.subtopic
            HAVING avg_pct < 70 OR struggling_count >= 2
            ORDER BY
              (struggling_count * 100.0 / NULLIF(students_attempted, 0)) DESC,
              avg_pct ASC
            LIMIT 8
        """)
        weak_rows = cursor.fetchall()

        # Collect every struggling student id so we can resolve names in one
        # follow-up query rather than N round-trips.
        weak_ids = set()
        for row in weak_rows:
            ids_csv = row[5]
            if ids_csv:
                for x in str(ids_csv).split(","):
                    x = x.strip()
                    if x:
                        weak_ids.add(int(x))

        # 2) Inactive students. Pure inactivity bucket — silent 5+ days or
        # never active. Low-accuracy students show up under Weakest Topics
        # (their names are in the struggling-students expansion), so this
        # section is just "who needs a WhatsApp nudge right now."
        cursor.execute(f"""
            SELECT
              u.id, u.name, u.email,
              MAX(qa.attempted_at) AS last_active,
              ROUND(AVG(qa.percentage), 1) AS recent_avg_pct,
              COUNT(qa.id) AS recent_attempts
            FROM users u
            LEFT JOIN quiz_attempts qa
              ON qa.user_id = u.id
             AND qa.attempted_at >= NOW() - INTERVAL 14 DAY
             {qt_clause}
            WHERE u.is_teacher = FALSE
            GROUP BY u.id, u.name, u.email
            HAVING last_active IS NULL
                OR DATEDIFF(NOW(), last_active) >= 5
            ORDER BY
              CASE WHEN last_active IS NULL THEN 0 ELSE 1 END,
              last_active ASC
            LIMIT 20
        """)
        inactive_rows = cursor.fetchall()

        # 3) Week at a glance / class pulse. ALWAYS unfiltered — these are
        # the "is the class healthy?" headline stats that sit above the toggle.
        # The toggle only narrows the deep-dive sections (weakest topics,
        # inactive list, consistency rows), not these top-line numbers.
        cursor.execute("""
            SELECT
              (SELECT COUNT(*) FROM users WHERE is_teacher = FALSE) AS total_students,
              COUNT(DISTINCT qa.user_id) AS active_students,
              COUNT(qa.id) AS total_quizzes,
              ROUND(AVG(qa.percentage), 1) AS class_avg_pct,
              ROUND(
                100.0 * SUM(CASE WHEN qa.percentage >= 60 THEN 1 ELSE 0 END)
                / NULLIF(COUNT(qa.id), 0),
                1
              ) AS pass_rate_pct
            FROM quiz_attempts qa
            JOIN users u ON u.id = qa.user_id
            WHERE u.is_teacher = FALSE
              AND qa.attempted_at >= NOW() - INTERVAL 7 DAY
        """)
        week_row = cursor.fetchone() or (0, 0, 0, None, None)

        # 3b) Class-pulse inactive count: how many students would the Inactive
        # list show if the filter were "all"? Used by the Inactive tile so the
        # tile stays stable even when the filter narrows the list below.
        cursor.execute("""
            SELECT COUNT(*) FROM (
              SELECT u.id
              FROM users u
              LEFT JOIN quiz_attempts qa
                ON qa.user_id = u.id
               AND qa.attempted_at >= NOW() - INTERVAL 14 DAY
              WHERE u.is_teacher = FALSE
              GROUP BY u.id
              HAVING MAX(qa.attempted_at) IS NULL
                  OR DATEDIFF(NOW(), MAX(qa.attempted_at)) >= 5
            ) AS x
        """)
        inactive_count_class = int((cursor.fetchone() or (0,))[0] or 0)

        # 4) Per-student consistency: how many of the last 7 days each student
        # showed up, plus quiz volume and streak length. Sorted most-consistent
        # first so the teacher can scan top-to-bottom.
        #
        # Volume is reported BOTH ways ("show both" policy):
        #   - attempts  = every submitted attempt, retakes included (effort)
        #   - quizzes   = distinct quizzes, retakes collapsed onto their
        #                 parent_attempt_id root (coverage)
        # and BOTH windows: last-7-days and all-time — so a student who did
        # 5 quizzes last month and 1 this week reads "1 this week · 6 all-time"
        # instead of the misleading bare "1 quiz".
        cursor.execute(f"""
            SELECT
              u.id, u.name,
              COUNT(DISTINCT DATE(qa.attempted_at)) AS days_active_7d,
              COUNT(qa.id) AS quizzes_7d,
              COUNT(DISTINCT COALESCE(qa.parent_attempt_id, qa.id)) AS distinct_quizzes_7d,
              ROUND(AVG(qa.percentage), 1) AS avg_pct_7d,
              MAX(qa.attempted_at) AS last_active,
              COALESCE(s.current_streak, 0) AS current_streak,
              COALESCE(s.longest_streak, 0) AS longest_streak,
              COALESCE(tot.attempts_all, 0) AS attempts_all,
              COALESCE(tot.quizzes_all, 0) AS quizzes_all
            FROM users u
            LEFT JOIN quiz_attempts qa
              ON qa.user_id = u.id
             AND qa.attempted_at >= NOW() - INTERVAL 7 DAY
             {qt_clause}
            LEFT JOIN streaks s ON s.user_id = u.id
            LEFT JOIN (
                SELECT qa.user_id,
                       COUNT(*) AS attempts_all,
                       COUNT(DISTINCT COALESCE(qa.parent_attempt_id, qa.id)) AS quizzes_all
                FROM quiz_attempts qa
                WHERE 1=1{qt_clause}
                GROUP BY qa.user_id
            ) tot ON tot.user_id = u.id
            WHERE u.is_teacher = FALSE
            GROUP BY u.id, u.name, s.current_streak, s.longest_streak,
                     tot.attempts_all, tot.quizzes_all
            ORDER BY days_active_7d DESC, quizzes_7d DESC, u.name ASC
        """)
        consistency_rows = cursor.fetchall()

        # Resolve struggling-student ids -> names in one query.
        id_to_name = {}
        if weak_ids:
            fmt = ",".join(["%s"] * len(weak_ids))
            cursor.execute(
                f"SELECT id, name FROM users WHERE id IN ({fmt})",
                tuple(weak_ids)
            )
            for uid, uname in cursor.fetchall():
                id_to_name[int(uid)] = uname or "Student"

        # Shape the response.
        weakest_topics = []
        for row in weak_rows:
            subtopic, students_attempted, attempts, avg_pct, struggling_count, ids_csv = row
            ids = []
            if ids_csv:
                for x in str(ids_csv).split(","):
                    x = x.strip()
                    if x:
                        ids.append(int(x))
            weakest_topics.append({
                "topic":               subtopic,
                "students_attempted":  int(students_attempted or 0),
                "attempts":            int(attempts or 0),
                "avg_pct":             float(avg_pct) if avg_pct is not None else None,
                "struggling_count":    int(struggling_count or 0),
                "struggling_students": [
                    {"id": uid, "name": id_to_name.get(uid, "Student")}
                    for uid in ids
                ],
            })

        now = datetime.utcnow()
        inactive_students = []
        for row in inactive_rows:
            uid, uname, uemail, last_active, recent_avg_pct, recent_attempts = row
            inactive_students.append({
                "id":              int(uid),
                "name":            uname or "Student",
                "email":           uemail,
                "last_active":     str(last_active) if last_active else None,
                "days_since":      ((now - last_active).days if last_active else None),
                "recent_avg_pct":  float(recent_avg_pct) if recent_avg_pct is not None else None,
                "recent_attempts": int(recent_attempts or 0),
            })

        total_students, active_students, total_quizzes, class_avg_pct, pass_rate_pct = week_row
        avg_quizzes_per_active = (
            round(int(total_quizzes or 0) / int(active_students), 1)
            if active_students else 0
        )

        # Shape per-student consistency rows and the class-level summary.
        consistency = []
        days_sum = 0
        days_count = 0
        streak_3plus = 0
        for row in consistency_rows:
            (uid, uname, days, quizzes, distinct_quizzes, avg_pct_7d, last_active,
             cur_streak, long_streak, attempts_all, quizzes_all) = row
            days_int = int(days or 0)
            consistency.append({
                "id":             int(uid),
                "name":           uname or "Student",
                "days_active_7d": days_int,
                # quizzes_7d kept for backwards compat = attempts this week.
                "quizzes_7d":          int(quizzes or 0),
                "attempts_7d":         int(quizzes or 0),
                "distinct_quizzes_7d": int(distinct_quizzes or 0),
                "attempts_all":        int(attempts_all or 0),
                "quizzes_all":         int(quizzes_all or 0),
                "avg_pct_7d":     float(avg_pct_7d) if avg_pct_7d is not None else None,
                "last_active":    str(last_active) if last_active else None,
                "current_streak": int(cur_streak or 0),
                "longest_streak": int(long_streak or 0),
            })
            days_sum += days_int
            days_count += 1
            if int(cur_streak or 0) >= 3:
                streak_3plus += 1

        consistency_summary = {
            "avg_days_active":            round(days_sum / days_count, 1) if days_count else 0,
            "students_with_streak_3plus": streak_3plus,
            "total_students_listed":      days_count,
        }

        return {
            "success":      True,
            "generated_at": now.isoformat() + "Z",
            "window_days":  7,
            "filter":       qt,
            "week_at_a_glance": {
                "total_students":         int(total_students or 0),
                "active_students":        int(active_students or 0),
                "total_quizzes":          int(total_quizzes or 0),
                "class_avg_pct":          float(class_avg_pct) if class_avg_pct is not None else None,
                "avg_quizzes_per_active": float(avg_quizzes_per_active),
                "pass_rate_pct":          float(pass_rate_pct) if pass_rate_pct is not None else None,
                "inactive_count":         inactive_count_class,
            },
            "weakest_topics":      weakest_topics,
            "inactive_students":   inactive_students,
            "consistency":         consistency,
            "consistency_summary": consistency_summary,
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in /api/teacher/overview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/teacher/students/{user_id}")
def get_teacher_student_detail(user_id: int, authorization: str = Header(None)):
    """Single-student drill-in: card data + the last 50 attempts (summary only).
    Teachers click a student row in the dashboard to land here. Each attempt
    is a slim summary; click an attempt to fetch its full per-question detail
    via /api/teacher/attempts/{id}."""
    require_teacher(authorization)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, name, email, created_at, is_teacher FROM users WHERE id = %s",
            (user_id,)
        )
        u = cursor.fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="Student not found")
        u_id, u_name, u_email, u_created, u_is_teacher = u
        if u_is_teacher:
            raise HTTPException(status_code=400, detail="Target is a teacher, not a student")

        cursor.execute(
            "SELECT current_streak, longest_streak FROM streaks WHERE user_id = %s",
            (u_id,)
        )
        srow = cursor.fetchone()
        current_streak, longest_streak = (int(srow[0] or 0), int(srow[1] or 0)) if srow else (0, 0)

        cursor.execute(
            "SELECT COUNT(*), ROUND(AVG(percentage), 1) FROM quiz_attempts WHERE user_id = %s",
            (u_id,)
        )
        trow = cursor.fetchone() or (0, None)
        total_attempts = int(trow[0] or 0)
        lifetime_avg_pct = float(trow[1]) if trow[1] is not None else None

        cursor.execute("""
            SELECT id, name, subtopic, difficulty, score, percentage, total_questions,
                   time_spent_seconds, quiz_type, attempted_at
            FROM quiz_attempts
            WHERE user_id = %s
            ORDER BY attempted_at DESC
            LIMIT 50
        """, (u_id,))
        rows = cursor.fetchall()

        attempts = []
        for r in rows:
            a_id, a_name, a_sub, a_diff, a_score, a_pct, a_total, a_time, a_type, a_when = r
            attempts.append({
                "id":              int(a_id),
                "name":            a_name,
                "subtopic":        a_sub,
                "difficulty":      a_diff,
                "score":           int(a_score or 0),
                "percentage":      int(a_pct or 0),
                "total_questions": int(a_total or 0),
                "time_spent_seconds": int(a_time or 0),
                "quiz_type":       (a_type or "practice"),
                "attempted_at":    str(a_when) if a_when else None,
            })

        return {
            "success": True,
            "student": {
                "id":               int(u_id),
                "name":             u_name or "Student",
                "email":            u_email,
                "created_at":       str(u_created) if u_created else None,
                "current_streak":   current_streak,
                "longest_streak":   longest_streak,
                "total_attempts":   total_attempts,
                "lifetime_avg_pct": lifetime_avg_pct,
            },
            "attempts": attempts,
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in /api/teacher/students/{user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/teacher/attempts/{attempt_id}")
def get_teacher_attempt_detail(attempt_id: int, authorization: str = Header(None)):
    """Full per-question review for one attempt. Same shape as the student
    retake endpoint, but gated by is_teacher instead of attempt ownership."""
    require_teacher(authorization)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, user_id, name, difficulty, subtopic, score, percentage,
                   total_questions, time_spent_seconds, questions_data,
                   quiz_type, attempted_at
            FROM quiz_attempts
            WHERE id = %s
        """, (attempt_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Attempt not found")

        (a_id, a_user_id, a_name, a_diff, a_sub, a_score, a_pct, a_total,
         a_time, a_qjson, a_type, a_when) = row

        import json
        questions_data = []
        try:
            questions_data = json.loads(a_qjson) if a_qjson else []
        except Exception as je:
            print(f"⚠️  Failed to parse questions_data for attempt {a_id}: {je}")
            questions_data = []

        # Re-hydrate skinny legacy rows from the in-memory cache so the review
        # page has full question/options/diagram fields even on attempts saved
        # before the full questions_data shape existed. Mirrors the retake
        # endpoint logic.
        if any(not q.get('options') and not q.get('table_rows') for q in questions_data):
            if not cache.is_loaded:
                cache.load_questions()
            by_qno  = {q.qno: q for q in cache.questions if q.qno}
            by_text = {q.question_text.strip(): q for q in cache.questions if q.question_text}
            for i, q in enumerate(questions_data):
                if q.get('options') or q.get('table_rows'):
                    continue
                full = (by_qno.get(q.get('qno'))
                        or by_text.get((q.get('question_text') or '').strip()))
                if not full:
                    continue
                questions_data[i] = {
                    'qno':                  full.qno,
                    'uid':                  full.uid,
                    'subtopic':             full.subtopic,
                    'difficulty':           full.difficulty,
                    'level':                full.level,
                    'question_text':        full.question_text,
                    'options':              full.options,
                    'answer':               full.answer,
                    'option_type':          full.option_type,
                    'table_headers':        full.table_headers,
                    'table_header_levels':  full.table_header_levels,
                    'table_header_colspan': full.table_header_colspan,
                    'table_rows':           full.table_rows,
                    'diagram_file_id':      full.diagram_file_id,
                    'options_image_uid':    full.options_image_uid,
                    'image_url':            full.image_url,
                    'setup_image_url':      full.setup_image_url,
                    'explanation':          full.explanation,
                    'user_answer':          q.get('user_answer'),
                    'correct_answer':       q.get('correct_answer') or full.answer,
                    'is_correct':           q.get('is_correct', False),
                }

        # Resolve diagram + options-image file IDs to URLs the frontend can
        # render directly (mirrors the retake endpoint).
        for q in questions_data:
            if q.get('diagram_file_id'):
                actual_file_id = q['diagram_file_id']
                if actual_file_id:
                    q['setup_image_url'] = f"{PUBLIC_BASE_URL}/api/image/{actual_file_id}"
            if q.get('option_type') == 'IMAGE' and q.get('options_image_uid'):
                options_file_id = q['options_image_uid']
                if options_file_id:
                    q['image_url'] = f"{PUBLIC_BASE_URL}/api/image/{options_file_id}"
            elif q.get('setup_image_url') and not q.get('image_url'):
                q['image_url'] = q['setup_image_url']

        cursor.execute("SELECT name, email FROM users WHERE id = %s", (a_user_id,))
        urow = cursor.fetchone() or (None, None)
        student_name, student_email = urow

        return {
            "success": True,
            "attempt": {
                "id":                int(a_id),
                "user_id":           int(a_user_id),
                "student_name":      student_name or "Student",
                "student_email":     student_email,
                "name":              a_name,
                "difficulty":        a_diff,
                "subtopic":          a_sub,
                "score":             int(a_score or 0),
                "percentage":        int(a_pct or 0),
                "total_questions":   int(a_total or 0),
                "time_spent_seconds": int(a_time or 0),
                "quiz_type":         (a_type or "practice"),
                "attempted_at":      str(a_when) if a_when else None,
                "questions":         questions_data,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in /api/teacher/attempts/{attempt_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.delete("/api/teacher/students/{user_id}")
def delete_teacher_student(user_id: int, authorization: str = Header(None)):
    """Permanently delete a student account and ALL of their data.

    Removing the `users` row cascades to every child table
    (quiz_attempts, streaks, rank_history, daily_challenges, user_rewards,
    user_subject_ranks — all wired ON DELETE CASCADE), so this single DELETE
    wipes the student's entire footprint. Irreversible.

    Guards: teacher-only (is_teacher JWT claim); refuses to delete another
    teacher account, and refuses to delete the caller's own account."""
    payload = require_teacher(authorization)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, name, email, is_teacher FROM users WHERE id = %s",
            (user_id,),
        )
        u = cursor.fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="Student not found")
        u_id, u_name, u_email, u_is_teacher = u
        if u_is_teacher:
            raise HTTPException(status_code=400, detail="Cannot delete a teacher account")
        if int(u_id) == int(payload.get('user_id') or 0):
            raise HTTPException(status_code=400, detail="You cannot delete your own account")

        cursor.execute("DELETE FROM users WHERE id = %s", (u_id,))
        conn.commit()
        print(f"🗑️  Teacher {payload.get('email')} deleted student {u_email} (id={u_id})")
        return {
            "success": True,
            "deleted": {"id": int(u_id), "name": u_name or "Student", "email": u_email},
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        print(f"❌ Error in DELETE /api/teacher/students/{user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.post("/api/teacher/students/{user_id}/reset-password")
def reset_teacher_student_password(user_id: int, authorization: str = Header(None)):
    """Reset a student's password to the default (`DEFAULT_RESET_PASSWORD`).

    Only the bcrypt hash of the default is stored; the plaintext default is
    returned so the teacher can pass it on. The student can change it afterwards
    from Settings. For a Google-only account (no prior password), this also
    enables email+password login as a fallback.

    Teacher-only; refuses to target another teacher account."""
    payload = require_teacher(authorization)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, name, email, is_teacher, google_id, password_hash FROM users WHERE id = %s",
            (user_id,),
        )
        u = cursor.fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="Student not found")
        u_id, u_name, u_email, u_is_teacher, u_google_id, u_pw_hash = u
        if u_is_teacher:
            raise HTTPException(status_code=400, detail="Cannot reset a teacher account's password here")

        temp_password = DEFAULT_RESET_PASSWORD
        new_hash = hash_password(temp_password)
        cursor.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (new_hash, u_id),
        )
        conn.commit()
        print(f"🔑 Teacher {payload.get('email')} reset password for student {u_email} (id={u_id})")

        # A Google-only account had no password before; the temp password now
        # also works as an email+password fallback. Flag it so the UI can note
        # that the student normally signs in with Google.
        google_only = bool(u_google_id) and not u_pw_hash
        return {
            "success": True,
            "student": {"id": int(u_id), "name": u_name or "Student", "email": u_email},
            "temp_password": temp_password,
            "google_account": google_only,
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        print(f"❌ Error in POST /api/teacher/students/{user_id}/reset-password: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ── Entry point ──────────────────────────────────────────────────────────
# Runs the FastAPI app via uvicorn when executed directly (`python
# quiz_backend.py`). Reload is OFF because the startup hook performs schema
# migrations and reload would run them twice + spawn duplicate workers.
# For dev hot-reload, run instead:  uvicorn quiz_backend:app --reload
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
