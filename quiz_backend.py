"""
HabitGo Backend - FastAPI
Fetches questions from Google Sheet, images from Google Drive
Returns filtered quizzes based on difficulty, subtopic, and count
"""

import os
import random
from typing import List, Optional, Tuple, Dict
from collections import defaultdict
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
QUESTION_FOLDER_ID = '10TtAVgxTsczSFxIrkwSSy_KFQlebWCiX'
SHEET_NAME = 'Physics'  # Just the sheet name, no range

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
JWT_EXPIRATION_HOURS = 24

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
    sheets_service = build('sheets', 'v4', credentials=credentials)
    drive_service = build('drive', 'v3', credentials=credentials)
except Exception as e:
    print(f"⚠️  Warning: Could not initialize Google APIs: {e}")
    sheets_service = None
    drive_service = None

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

class LoginRequest(BaseModel):
    email: str
    password: str

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

                    # Get flat headers for mapping (use last header row for simple cases)
                    flat_headers = header_rows[-1] if header_rows else []

                    # Map values to headers
                    for j, header in enumerate(flat_headers):
                        if j == 0:
                            row_data[header] = value
                        elif j < len(parts):
                            row_data[header] = parts[j]

                    row_data['_letter'] = letter
                    # Positional cell values (letter prefix stripped) so the
                    # frontend can render the row even when the table has no
                    # header row to key the values by.
                    row_data['_cells'] = [value] + parts[1:]
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

def create_jwt_token(user_id: int, email: str) -> str:
    """Create a JWT token for the user"""
    payload = {
        'user_id': user_id,
        'email': email,
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

    def load_file_map(self):
        """Pre-load all files from QUESTION_FOLDER_ID into memory for fast lookup"""
        if not drive_service or self.file_map:
            return  # Already loaded or service not available

        try:
            print("📁 Pre-loading file map from Google Drive...")
            query = f"'{QUESTION_FOLDER_ID}' in parents and trashed=false"
            results = drive_service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name)',
                pageSize=1000
            ).execute()

            files = results.get('files', [])

            # Create a mapping: lowercase name → file ID
            for f in files:
                name = f['name']
                file_id = f['id']

                # Store by exact name and lowercase name
                self.file_map[name] = file_id
                self.file_map[name.lower()] = file_id

                # Also store without extension for flexibility
                if '.' in name:
                    name_no_ext = name.rsplit('.', 1)[0]
                    self.file_map[name_no_ext] = file_id
                    self.file_map[name_no_ext.lower()] = file_id

            print(f"✅ Loaded {len(files)} files into memory for fast lookup")
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
        if self.is_loaded:
            return

        if not sheets_service:
            raise RuntimeError("Google Sheets API not initialized")

        try:
            result = sheets_service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=SHEET_NAME
            ).execute()

            rows = result.get('values', [])
            if not rows:
                print("⚠️  No questions found in sheet")
                return

            # First row is header
            headers = rows[0]
            print(f"📋 Headers: {headers}")

            # Create column index map
            col_map = {header: idx for idx, header in enumerate(headers)}

            # Use 'Topic' if available, fallback to 'Subtopic' for backward compatibility
            topic_col = 'Topic' if 'Topic' in col_map else 'Subtopic'
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
                        self.setup_info_map[main_uid] = {
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
                        subtopic=subtopic,
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
                        options_image_uid=options_image_uid
                    )
                    self.questions.append(question)

                except Exception as e:
                    print(f"⚠️  Error parsing row {row_idx}: {e}")
                    continue

            self.is_loaded = True
            print(f"✅ Loaded {len(self.questions)} questions from sheet")

        except Exception as e:
            print(f"❌ Error loading questions: {e}")
            raise

    def resolve_file_id(self, potential_file_id: str) -> Optional[str]:
        """
        Resolve a potential file ID or filename to an actual Google Drive file ID.
        If it's already a valid file ID (starts with digit, contains alphanumeric), return it.
        If it's a filename, look it up in file_map and get the actual file ID.
        """
        if not potential_file_id:
            return None

        print(f"      [resolve_file_id] Input: {potential_file_id}")
        print(f"      [resolve_file_id] File map size: {len(self.file_map)}")

        # If it looks like a real Google Drive file ID (contains hyphen/underscore, mostly alphanumeric)
        # and exists in file_map as a key, it's a file ID
        # Try exact then lowercase, with and without common extensions.
        # file_map holds both original- and lower-case keys, so checking the
        # lowercased form makes the whole lookup case-insensitive.
        for base in [potential_file_id, potential_file_id.lower()]:
            if base in self.file_map:
                result = self.file_map[base]
                print(f"      [resolve_file_id] Found match: {result}")
                return result
            for ext in ['.png', '.jpg', '.jpeg', '.gif']:
                if base + ext in self.file_map:
                    result = self.file_map[base + ext]
                    print(f"      [resolve_file_id] Found with extension {ext}: {result}")
                    return result

        # If nothing found, assume it's already a file ID
        # (might be one that doesn't exist in our folder)
        print(f"      [resolve_file_id] Not found in map, assuming it's a file ID already")
        return potential_file_id

    def get_image_url(self, uid: str) -> Optional[str]:
        """Get Google Drive image URL by UID/filename using pre-loaded file map"""
        if uid in self.image_url_cache:
            return self.image_url_cache[uid]

        if not drive_service or not self.file_map:
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
        if not self.is_loaded:
            self.load_questions()

        subtopics = set()
        for q in self.questions:
            if q.subtopic and q.subtopic.lower() != 'question setup':
                subtopics.add(q.subtopic)

        return sorted(list(subtopics))

    def get_unique_difficulties(self) -> List[str]:
        """Get all unique difficulties"""
        if not self.is_loaded:
            self.load_questions()

        difficulties = set()
        for q in self.questions:
            if q.difficulty:
                difficulties.add(q.difficulty)

        return sorted(list(difficulties))

    def get_unique_levels(self) -> List[str]:
        """Get all unique levels (streams/subjects)"""
        if not self.is_loaded:
            self.load_questions()

        levels = set()
        for q in self.questions:
            if q.level:
                levels.add(q.level)

        return sorted(list(levels))

    def get_unique_subjects(self) -> List[str]:
        """Get all unique subjects (defaults to ['Physics'] when no Subject column)."""
        if not self.is_loaded:
            self.load_questions()
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
        if not self.is_loaded:
            self.load_questions()

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
            lv = str(level).strip().lower()
            if lv in ('pure', 'nonpure', 'non-pure', 'combined'):
                want_nonpure = lv != 'pure'
                filtered = [q for q in filtered if _is_nonpure(q.level) == want_nonpure]
            else:
                filtered = [q for q in filtered if q.level and q.level.lower() == lv]

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
    if not cache.is_loaded:
        cache.load_questions()

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
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "questions_loaded": cache.is_loaded}

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
    "Physical Quantities, Units and Measurements",
    "Kinematics",
    "Force and Pressure",
    "Dynamics",
    "Turning Effect of Forces",
    "Energy",
    "Kinetic Particle Model of Matter",
    "Thermal Processes",
    "General Wave Properties",
    "Electromagnetic Spectrum",
    "Light",
    "Electric Charge and Current of Electricity",
    "D.C. Circuits",
    "Practical Electricity",
    "Magnetism and Electromagnetism",
    "Radioactivity",
]


def _norm_topic(name):
    """Normalise a topic name for matching: strip a leading number prefix
    (e.g. "2. Kinematics"), lowercase, trim."""
    s = str(name).strip()
    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    if i > 0:
        rest = s[i:].lstrip(".) ")
        if rest:
            s = rest
    return s.strip().lower()


def _topic_sort_key(name, order):
    """Sort key placing topics in the given syllabus `order`. Unknown topics
    fall to the end, alphabetically."""
    key = _norm_topic(name)
    for idx, canon in enumerate(order):
        if _norm_topic(canon) == key:
            return (idx, key)
    return (len(order), key)


def _is_nonpure(level_value):
    """Non-pure physics is labelled '4E5N' in the sheet's Level column.
    Everything else counts as pure physics."""
    return '4e5n' in str(level_value or '').strip().lower()


@app.get("/api/subtopics", response_model=List[str])
async def get_subtopics(level: str = None):
    """Available topics, optionally filtered to one physics level.
    `level` = 'pure' or 'nonpure' (non-pure == the sheet's '4E5N' Level)."""
    try:
        if not cache.is_loaded:
            cache.load_questions()

        cat = (level or '').strip().lower()
        if cat in ('pure', 'nonpure', 'non-pure', 'combined'):
            want_nonpure = cat != 'pure'
            order = COMBINED_TOPIC_ORDER if want_nonpure else PURE_TOPIC_ORDER
            # Surface the full syllabus list, in order. Where a topic has
            # questions, use the exact name from the sheet so quiz generation
            # matches; where it has none, fall back to the canonical name.
            by_norm = {}
            for q in cache.questions:
                if (q.subtopic and q.subtopic.lower() != 'question setup'
                        and _is_nonpure(q.level) == want_nonpure):
                    by_norm.setdefault(_norm_topic(q.subtopic), q.subtopic)
            subtopics = [by_norm.get(_norm_topic(c), c) for c in order]
        else:
            subtopics = sorted(cache.get_unique_subtopics(),
                               key=lambda s: _topic_sort_key(s, PURE_TOPIC_ORDER))

        print(f"  ✅ /api/subtopics (level={cat or 'all'}) -> {len(subtopics)}")
        return subtopics
    except Exception as e:
        print(f"  ❌ Error in /api/subtopics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/difficulties", response_model=List[str])
async def get_difficulties():
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
async def get_levels():
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
async def create_quiz(request: QuizRequest, authorization: str = Header(None)):
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
            if not single_topic and request.level:
                _lv = str(request.level).strip().lower()
                if _lv in ('pure', 'nonpure', 'non-pure', 'combined'):
                    _order = COMBINED_TOPIC_ORDER if _lv != 'pure' else PURE_TOPIC_ORDER
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
            selected_questions = random.sample(filtered_questions, request.count)

        # Create deep copies of selected questions to avoid modifying cached originals
        from copy import deepcopy
        selected_questions = [deepcopy(q) for q in selected_questions]

        # Attach setup information and set image URLs for each question
        print(f"\nDEBUG: Processing {len(selected_questions)} selected questions")
        print(f"DEBUG: setup_info_map keys: {list(cache.setup_info_map.keys())}")

        for question in selected_questions:
            print(f"\nDEBUG: Processing question: {question.uid}")

            # Try to find setup info (check with and without trailing dash)
            setup_uid = question.uid.rstrip('-')  # Remove trailing dash if present
            setup_info = cache.setup_info_map.get(setup_uid)

            if setup_info:
                print(f"  ✅ Found setup info for {setup_uid}")
                # Prepend setup text to question text
                if setup_info['text']:
                    question.question_text = setup_info['text'] + "\n\n" + question.question_text
                    print(f"  ✅ Added setup text")
                else:
                    print(f"  ⚠️  Setup info has no text")

            # Use setup diagram if question doesn't have its own
            if not question.diagram_file_id and setup_info and setup_info.get('file_id'):
                question.diagram_file_id = setup_info['file_id']
                print(f"  ✅ Using setup diagram from setup row: {setup_info['file_id']}")
            elif not question.diagram_file_id:
                print(f"  ℹ️  No diagram available")

            # Always set setup_image_url if diagram exists (for frontend fallback)
            if question.diagram_file_id:
                # Resolve potential filename to actual Google Drive file ID
                actual_file_id = cache.resolve_file_id(question.diagram_file_id)
                if actual_file_id:
                    # Use backend image proxy endpoint
                    question.setup_image_url = f"{PUBLIC_BASE_URL}/api/image/{actual_file_id}"
                    print(f"  ✅ Setup diagram available (resolved to {actual_file_id[:20]}...)")
                else:
                    print(f"  ⚠️  Could not resolve diagram file ID: {question.diagram_file_id}")

            # Set image_url based on question type:
            # - For IMAGE type: ONLY use options image (if it exists)
            # - For non-IMAGE type: Use setup diagram

            if question.option_type == 'IMAGE':
                # IMAGE type: only show options image if available
                if question.options_image_uid:
                    print(f"  IMAGE type → Searching for options image: {question.options_image_uid}")
                    # Resolve options image UID to file ID
                    options_file_id = cache.resolve_file_id(question.options_image_uid)
                    if options_file_id:
                        # Use backend image proxy endpoint
                        question.image_url = f"{PUBLIC_BASE_URL}/api/image/{options_file_id}"
                        print(f"  ✅ Options image found (resolved to {options_file_id[:20]}...)")
                    else:
                        print(f"  ⚠️  Options image not found - will show setup diagram instead")
                else:
                    print(f"  ⚠️  IMAGE type but no options_image_uid - will use setup diagram")
            else:
                # Non-IMAGE type (TEXT, TABLE): use setup diagram for image_url
                if question.setup_image_url:
                    question.image_url = question.setup_image_url
                    print(f"  ✅ Setup diagram set as image_url")

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
async def get_questions_by_category():
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
async def get_questions_by_type_endpoint(qtype: str):
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
async def get_category_statistics_endpoint():
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
async def export_questions_by_category():
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
_IMAGE_CACHE = {}
_IMAGE_CACHE_MAX = 300
_IMAGE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
    "Content-Disposition": "inline; filename=image.png",
}


@app.get("/api/image/{file_id}")
async def serve_image(file_id: str):
    """
    Backend image proxy endpoint.
    Serves Google Drive images, cached in memory after the first fetch.
    Bypasses Google Drive embedding restrictions.
    """
    try:
        # Fast path — already in the in-memory cache.
        cached = _IMAGE_CACHE.get(file_id)
        if cached is not None:
            return Response(content=cached, media_type="image/png", headers=_IMAGE_HEADERS)

        if not drive_service:
            raise HTTPException(status_code=500, detail="Google Drive service not initialized")

        # Download file from Google Drive
        request = drive_service.files().get_media(fileId=file_id)
        downloader = request.execute()
        if isinstance(downloader, bytes):
            data = downloader
        else:
            # If it's a stream, read it
            buf = BytesIO()
            while True:
                chunk = downloader.read(8192)
                if not chunk:
                    break
                buf.write(chunk)
            data = buf.getvalue()

        # Store in the cache (simple FIFO eviction once the cap is reached).
        if len(_IMAGE_CACHE) >= _IMAGE_CACHE_MAX:
            try:
                _IMAGE_CACHE.pop(next(iter(_IMAGE_CACHE)))
            except StopIteration:
                pass
        _IMAGE_CACHE[file_id] = data

        return Response(content=data, media_type="image/png", headers=_IMAGE_HEADERS)

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error serving image {file_id}: {e}")
        raise HTTPException(status_code=404, detail=f"Could not load image: {str(e)}")


# ============================================================================
# AUTHENTICATION ENDPOINTS
# ============================================================================

@app.post("/api/auth/signup", response_model=AuthResponse)
async def signup(request: SignupRequest):
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
                "INSERT INTO users (email, password_hash, name) VALUES (%s, %s, %s)",
                (request.email, password_hash, request.name)
            )
            conn.commit()

            user_id = cursor.lastrowid

            # Create JWT token
            token = create_jwt_token(user_id, request.email)

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
async def login(request: LoginRequest):
    """Login with email and password"""
    try:
        if not request.email or not request.password:
            raise HTTPException(status_code=400, detail="Email and password required")

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Find user by email
            cursor.execute(
                "SELECT id, email, password_hash, name, avatar_url, xp, gems, daily_goal FROM users WHERE email = %s",
                (request.email,)
            )
            user = cursor.fetchone()

            if not user:
                raise HTTPException(status_code=401, detail="Invalid email or password")

            user_id, email, password_hash, name, avatar_url, user_xp, user_gems, user_daily_goal = user

            # Verify password
            if not verify_password(request.password, password_hash):
                raise HTTPException(status_code=401, detail="Invalid email or password")

            # Create JWT token
            token = create_jwt_token(user_id, email)

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
async def google_login(request: GoogleLoginRequest):
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

            # Create JWT token
            token = create_jwt_token(user_id, email)

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
async def get_user_profile(authorization: str = Header(None)):
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
                "SELECT id, email, name, avatar_url, created_at FROM users WHERE id = %s",
                (user_id,)
            )
            user = cursor.fetchone()

            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            user_id, user_email, user_name, user_avatar, created_at = user

            return {
                'success': True,
                'user': {
                    'id': user_id,
                    'email': user_email,
                    'name': user_name,
                    'avatar_url': user_avatar,
                    'created_at': str(created_at)
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


class ProfileUpdateRequest(BaseModel):
    """Update the current user's display name and/or avatar."""
    name: Optional[str] = None
    avatar_url: Optional[str] = None  # data URL or external URL; None = leave unchanged


@app.put("/api/auth/profile")
async def update_user_profile(request: ProfileUpdateRequest, authorization: str = Header(None)):
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
async def submit_quiz_attempt(request: QuizSubmissionRequest, authorization: str = Header(None)):
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

        # Use frontend-provided score and questions if available (for retakes and accurate scoring)
        if request.score is not None and request.percentage is not None:
            score = request.score
            percentage = request.percentage
            print(f"✅ Using frontend-calculated score: {score}/{request.count} ({percentage}%)")
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
                        if answer_key(user_answer) == answer_key(correct_answer):
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
                # Use frontend-provided questions with calculated correctness
                full_questions_data = []
                for idx, q in enumerate(request.questions):
                    user_answer = request.user_answers.get(idx, "")
                    correct_answer = q.get('answer', "").strip()
                    is_correct = answer_key(user_answer) == answer_key(correct_answer)
                    q_copy = q.copy()
                    q_copy['is_correct'] = is_correct
                    q_copy['user_answer'] = user_answer
                    q_copy['correct_answer'] = correct_answer
                    full_questions_data.append(q_copy)
                full_questions_json = json.dumps(full_questions_data)
                print(f"✅ Storing {len(full_questions_data)} questions from frontend with correctness")
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
                    is_correct = answer_key(user_answer) == answer_key(correct_answer)
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

                # Daily-progress credit + streak award.
                try:
                    today_d = _effective_today(user_id)
                    _daily_subject = 'Physics'
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
                        cursor.execute(
                            "UPDATE daily_challenges SET xp = xp + %s "
                            "WHERE user_id = %s AND subject = %s AND challenge_date = %s",
                            (xp_delta, user_id, _daily_subject, today_d),
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
async def get_quiz_history(
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
async def get_attempt_details(attempt_id: int, authorization: str = Header(None)):
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
                        'index': q.get('index', i),
                        'user_answer': q.get('user_answer'),
                        'correct_answer': q.get('correct_answer') or full.answer,
                        'is_correct': q.get('is_correct', False),
                    }

            # Resolve diagram / options image file IDs to URLs
            for q in questions_data:
                if q.get('diagram_file_id'):
                    actual_file_id = cache.resolve_file_id(q['diagram_file_id'])
                    if actual_file_id:
                        q['setup_image_url'] = f"{PUBLIC_BASE_URL}/api/image/{actual_file_id}"

                if q.get('option_type') == 'IMAGE' and q.get('options_image_uid'):
                    options_file_id = cache.resolve_file_id(q['options_image_uid'])
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
async def get_quiz_for_retake(attempt_id: int, authorization: str = Header(None)):
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
                        # preserve attempt-specific fields from the saved row
                        'user_answer': q.get('user_answer'),
                        'correct_answer': q.get('correct_answer') or full.answer,
                        'is_correct': q.get('is_correct', False),
                    }

            # Set image URLs for questions
            for q in questions_data:
                if q.get('diagram_file_id'):
                    actual_file_id = cache.resolve_file_id(q['diagram_file_id'])
                    if actual_file_id:
                        q['setup_image_url'] = f"{PUBLIC_BASE_URL}/api/image/{actual_file_id}"

                if q.get('option_type') == 'IMAGE' and q.get('options_image_uid'):
                    options_file_id = cache.resolve_file_id(q['options_image_uid'])
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
async def get_user_stats(authorization: str = Header(None)):
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
    "C. lamp X" -> "C",  "C" -> "C",  "C) foo" -> "C". Falls back to the
    uppercased string when there is no A-D letter prefix."""
    if val is None:
        return ""
    s = str(val).strip()
    if s and s[0] in "ABCDabcd" and (len(s) == 1 or s[1] in ".) :-"):
        return s[0].upper()
    return s.upper()


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
async def get_subjects():
    """All subjects present in the question bank (defaults to [\'Physics\'])."""
    try:
        if not cache.is_loaded:
            cache.load_questions()
        return cache.get_unique_subjects()
    except Exception as e:
        print(f"\u274c Error in /api/subjects: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/placement/questions")
async def get_placement_questions(subject: str = "Physics", authorization: str = Header(None)):
    """Return 15 placement questions for a subject, spread across topics and difficulty."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        if not verify_jwt_token(authorization.replace("Bearer ", "")):
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        if not cache.is_loaded:
            cache.load_questions()

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
                actual_file_id = cache.resolve_file_id(question.diagram_file_id)
                if actual_file_id:
                    question.setup_image_url = f"{PUBLIC_BASE_URL}/api/image/{actual_file_id}"

            # Resolve the answer-options image for IMAGE questions; otherwise
            # fall back to the setup diagram as the question's image_url.
            if question.option_type == "IMAGE":
                if question.options_image_uid:
                    options_file_id = cache.resolve_file_id(question.options_image_uid)
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
async def submit_placement(request: PlacementSubmitRequest, authorization: str = Header(None)):
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
async def get_user_ranks(authorization: str = Header(None)):
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
                cursor2.execute("SELECT xp, gems, daily_goal FROM users WHERE id = %s", (user_id,))
                _xr = cursor2.fetchone()
                _xp   = int(_xr[0]) if _xr and _xr[0] is not None else 0
                _gems = int(_xr[1]) if _xr and _xr[1] is not None else 0
                _goal = int(_xr[2]) if _xr and _xr[2] is not None else 10
                # Freeze count: 1 if no streak row yet (everyone starts with
                # the free weekly freeze, matching /api/streak's default).
                cursor2.execute("SELECT freezes_available FROM streaks WHERE user_id = %s", (user_id,))
                _fr = cursor2.fetchone()
                _freezes = int(_fr[0]) if _fr and _fr[0] is not None else 1
            finally:
                cursor2.close()
                conn2.close()
        except Exception:
            _xp, _gems, _goal, _freezes = 0, 0, 10, 1

        return {
            "ranks":             ranks,
            "has_placement":     len(ranks) > 0,
            "progression":       compute_progression(_xp),
            "gems":              _gems,
            "daily_goal":        _goal,
            "freezes_available": _freezes,
            "freeze_cap":        GEMS_FREEZE_CAP,
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
async def get_daily_challenge(subject: str = "Physics", authorization: str = Header(None)):
    """Today's Daily Challenge: DAILY_CHALLENGE_LEN questions, weak-topic weighted."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get("user_id")

        if not cache.is_loaded:
            cache.load_questions()
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
                actual_file_id = cache.resolve_file_id(question.diagram_file_id)
                if actual_file_id:
                    question.setup_image_url = f"{PUBLIC_BASE_URL}/api/image/{actual_file_id}"
            if question.option_type == "IMAGE":
                if question.options_image_uid:
                    options_file_id = cache.resolve_file_id(question.options_image_uid)
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
async def submit_daily_challenge(request: DailyChallengeSubmitRequest, authorization: str = Header(None)):
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
async def get_streak(authorization: str = Header(None)):
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
async def get_streak_week(authorization: str = Header(None)):
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
async def get_leaderboard(
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
                cursor.execute("""
                    SELECT id, name, avatar_url, COALESCE(xp, 0) AS score
                    FROM users
                    WHERE name IS NOT NULL AND name <> ''
                    ORDER BY score DESC, id ASC
                """)
            elif period == "daily":
                cursor.execute("""
                    SELECT u.id, u.name, u.avatar_url,
                           COALESCE(SUM(dc.xp), 0) AS score
                    FROM users u
                    LEFT JOIN daily_challenges dc
                      ON dc.user_id = u.id AND dc.challenge_date = %s
                    WHERE u.name IS NOT NULL AND u.name <> ''
                    GROUP BY u.id, u.name, u.avatar_url
                    ORDER BY score DESC, u.id ASC
                """, (today,))
            else:  # weekly
                cursor.execute("""
                    SELECT u.id, u.name, u.avatar_url,
                           COALESCE(SUM(dc.xp), 0) AS score
                    FROM users u
                    LEFT JOIN daily_challenges dc
                      ON dc.user_id = u.id
                     AND dc.challenge_date BETWEEN %s AND %s
                    WHERE u.name IS NOT NULL AND u.name <> ''
                    GROUP BY u.id, u.name, u.avatar_url
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
        for idx, (uid, name, avatar_url, score) in enumerate(rows, start=1):
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
                "avatar_url": avatar_url,
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
    {"id": "sticker_pack",   "name": "CuriousLab Sticker Pack",  "cost": 50,  "emoji": "🌟", "type": "physical", "desc": "Physical sticker pack mailed to you."},
    {"id": "bookmark",       "name": "Holographic Bookmark",     "cost": 80,  "emoji": "🔖", "type": "physical", "desc": "Limited edition CuriousLab bookmark."},
    {"id": "avatar_dragon",  "name": "Dragon Avatar",            "cost": 120, "emoji": "🐉", "type": "avatar",   "desc": "Unlock a rare avatar.", "value": "🐉"},
    {"id": "notebook",       "name": "Curious Notebook",         "cost": 150, "emoji": "📓", "type": "physical", "desc": "A5 dotted notebook with formula reference."},
    {"id": "bubble_tea",     "name": "Bubble Tea Voucher",       "cost": 200, "emoji": "🧋", "type": "physical", "desc": "$8 voucher at participating shops."},
    {"id": "avatar_astro",   "name": "Astronaut Avatar",         "cost": 250, "emoji": "🧑‍🚀", "type": "avatar",   "desc": "Unlock a rare avatar.", "value": "🧑‍🚀"},
    {"id": "tshirt",         "name": "CuriousLab T-Shirt",       "cost": 400, "emoji": "👕", "type": "physical", "desc": "Premium cotton tee. Pick size on redeem."},
    {"id": "tuition_credit", "name": "1 Free Tuition Session",   "cost": 800, "emoji": "🎓", "type": "high-tier","desc": "One free 1-on-1 CuriousLab session."},
]
SHOP_BY_ID = {item["id"]: item for item in SHOP_CATALOGUE}


@app.get("/api/shop")
async def get_shop(authorization: str = Header(None)):
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
        finally:
            cursor.close()
            conn.close()

        return {
            "gems":      gems,
            "owned":     owned,
            "catalogue": SHOP_CATALOGUE,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in /api/shop: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ShopRedeemRequest(BaseModel):
    reward_id: str


@app.post("/api/shop/redeem")
async def redeem_reward(request: ShopRedeemRequest, authorization: str = Header(None)):
    """Spend gems to redeem one catalogue item. Idempotent-ish: UNIQUE constraint
    blocks double-redemption of the same item (returns 400 with a clear message)."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")
        payload = verify_jwt_token(authorization.replace("Bearer ", ""))
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.get("user_id")

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
        print("\U0001f4c1 Pre-loading file map...")
        cache.load_file_map()
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
# RUN SERVER
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    print("\U0001f3af Starting HabitGo Backend...")
    print("\U0001f4da API docs: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)
