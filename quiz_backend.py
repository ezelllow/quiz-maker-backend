"""
Quiz Maker Backend - FastAPI
Fetches questions from Google Sheet, images from Google Drive
Returns filtered quizzes based on difficulty, subtopic, and count
"""

import os
import random
from typing import List, Optional, Tuple, Dict
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from io import BytesIO
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import json
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
SHEET_NAME = 'Paper1'  # Just the sheet name, no range

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
            if not line or line.startswith('TABLE:'):
                continue

            # Check if this is a data row (starts with A, B, C, or D followed by ))
            is_data_row = line and line[0] in 'ABCD' and len(line) > 1 and line[1] == ')'

            if is_data_row:
                data_started = True

            if not data_started:
                # This is a header row
                header_parts = [h.strip() for h in line.split('|')]
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

def get_db_connection():
    """Get MySQL database connection"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed")

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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                        self.setup_info_map[main_uid] = {
                            'text': question_text,
                            'file_id': diagram_file_id  # Can be None
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
        if potential_file_id in self.file_map:
            result = self.file_map[potential_file_id]
            print(f"      [resolve_file_id] Found exact match: {result}")
            return result

        # Try with extensions
        for ext in ['.png', '.jpg', '.jpeg', '.gif']:
            filename = potential_file_id + ext
            if filename in self.file_map:
                result = self.file_map[filename]
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

    def get_filtered_questions(self, difficulty: Optional[str] = None,
                               subtopic: Optional[str] = None,
                               level: Optional[str] = None) -> List[Question]:
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

        # Filter by level (stream/subject)
        if level:
            filtered = [q for q in filtered if q.level and q.level.lower() == level.lower()]

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
    title="Quiz Maker API",
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

@app.get("/api/subtopics", response_model=List[str])
async def get_subtopics():
    """Get all available subtopics"""
    try:
        print(f"📌 /api/subtopics called. Cache loaded: {cache.is_loaded}, Questions count: {len(cache.questions)}")

        # Ensure questions are loaded
        if not cache.is_loaded:
            print("  ⚠️  Cache not loaded, loading now...")
            cache.load_questions()

        subtopics = cache.get_unique_subtopics()
        print(f"  ✅ Returning {len(subtopics)} subtopics: {subtopics[:5]}...")
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
            )
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


@app.get("/api/image/{file_id}")
async def serve_image(file_id: str):
    """
    Backend image proxy endpoint.
    Downloads image from Google Drive and serves it directly.
    Bypasses Google Drive embedding restrictions.
    """
    try:
        if not drive_service:
            raise HTTPException(status_code=500, detail="Google Drive service not initialized")

        print(f"🖼️  Serving image: {file_id}")

        # Download file from Google Drive
        request = drive_service.files().get_media(fileId=file_id)
        file_stream = BytesIO()

        downloader = request.execute()
        if isinstance(downloader, bytes):
            file_stream.write(downloader)
        else:
            # If it's a stream, read it
            while True:
                chunk = downloader.read(8192)
                if not chunk:
                    break
                file_stream.write(chunk)

        file_stream.seek(0)

        # Determine MIME type based on file ID or default to image
        mime_type = "image/png"

        print(f"✅ Served {file_id} ({len(file_stream.getvalue())} bytes)")

        return StreamingResponse(
            iter([file_stream.getvalue()]),
            media_type=mime_type,
            headers={
                "Cache-Control": "public, max-age=3600",
                "Content-Disposition": f"inline; filename=image.png"
            }
        )

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
                    'name': request.name
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
                "SELECT id, email, password_hash, name FROM users WHERE email = %s",
                (request.email,)
            )
            user = cursor.fetchone()

            if not user:
                raise HTTPException(status_code=401, detail="Invalid email or password")

            user_id, email, password_hash, name = user

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
                    'name': name
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
                "SELECT id, email, name FROM users WHERE google_id = %s",
                (google_id,)
            )
            user = cursor.fetchone()

            if user:
                # Existing Google user
                user_id, user_email, user_name = user
                print(f"✅ Google user logged in: {user_email}")
            else:
                # Check if email exists (from other signup method)
                cursor.execute(
                    "SELECT id, name FROM users WHERE email = %s",
                    (email,)
                )
                existing = cursor.fetchone()

                if existing:
                    # Link Google account to existing email
                    user_id, user_name = existing
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
                    'name': user_name
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
                "SELECT id, email, name, created_at FROM users WHERE id = %s",
                (user_id,)
            )
            user = cursor.fetchone()

            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            user_id, user_email, user_name, created_at = user

            return {
                'success': True,
                'user': {
                    'id': user_id,
                    'email': user_email,
                    'name': user_name,
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
                        if user_answer == correct_answer:
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
                    user_answer = request.user_answers.get(str(idx), "")
                    correct_answer = q.get('answer', "").strip()
                    is_correct = user_answer == correct_answer
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
                    user_answer = request.user_answers.get(str(idx), "")
                    correct_answer = q.answer.strip()
                    is_correct = user_answer == correct_answer
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
                 time_spent_seconds, questions_data, parent_attempt_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            ))
            conn.commit()
            attempt_id = cursor.lastrowid

            kind = "retake" if parent_attempt_id else "saved quiz"
            print(f"✅ Quiz attempt saved ({kind}): user={user_id}, score={score}/{request.count}, "
                  f"time={request.time_spent_seconds}s, parent={parent_attempt_id}")

            return {
                'success': True,
                'attempt_id': attempt_id,
                'score': score,
                'percentage': percentage,
                'total_questions': request.count,
                'message': f'Quiz saved! You scored {score}/{request.count} ({percentage}%)'
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
                sql += " AND parent_attempt_id IS NULL"
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


@app.on_event("startup")
async def startup_event():
    """Load questions and pre-cache files on startup"""
    try:
        print("🚀 Starting up...")
        print("💾 Initializing database...")
        init_database()
        print("📁 Pre-loading file map...")
        cache.load_file_map()
        print("📋 Loading questions...")
        cache.load_questions()
        print(f"📊 Available subtopics: {cache.get_unique_subtopics()}")
        print(f"📊 Available difficulties: {cache.get_unique_difficulties()}")

        stats = get_category_statistics()
        print("\n📊 Question Categories:")
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
    print("🎯 Starting Quiz Maker Backend...")
    print(f"📄 Spreadsheet ID: {SPREADSHEET_ID}")
    print(f"📁 Drive Folder ID: {QUESTION_FOLDER_ID}")
    print("\n💡 API will be available at http://localhost:8000")
    print("📖 Docs at http://localhost:8000/docs\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
