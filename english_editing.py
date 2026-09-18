"""
English -> Editing practice module.
====================================================================
Self-contained FastAPI router for the "Editing" question type used in
Singapore secondary-school English papers:

    A 12-line passage. The first and last lines are always correct.
    Of the middle 10 lines, 8 carry exactly one grammatical error and 2
    are clean. For each numbered line the student either ticks it (no
    error) or circles the wrong word and writes the correction.

Source of truth is a Google Sheet (one row per line) with columns:

    UID | Exercise | Difficulty | Question Text | Line # | Line Text |
    Incorrect Word | Correct Word | Error Code | Explanation

Any other column in the sheet is ignored.

The FIRST row of each UID group is the exercise header: it carries the
title, the difficulty and the full passage in `Question Text`. The rows
after it carry one numbered line each. The sheet stores only the ten
NUMBERED lines, so the unnumbered opening and closing lines are
reconstructed by subtracting the numbered lines from the full passage.

This module never imports quiz_backend directly (that would be a
circular import). quiz_backend calls `init(...)` once at module load to
inject the shared helpers, then `app.include_router(router)`.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/english", tags=["english"])

# ============================================================================
# CONFIGURATION
# ============================================================================

# "TWF Editing Questions" workbook. Its own spreadsheet, not a tab of the
# physics workbook -- same arrangement as the P6 Math bank.
#
# The service account in credentials.json must be able to READ this file, the
# same way it can read the physics workbook. If it can't, the sheet API
# answers 403 "The caller does not have permission" and the hub shows
# "Editing bank unavailable".
#
# Both settings are env-overridable so the bank can be moved -- to a tab of
# the main workbook, say -- without a code change:
#   EDITING_SPREADSHEET_ID=<id>
#   EDITING_SHEET_RANGE="TWF Editing"      (a tab name, or a range)
EDITING_SPREADSHEET_ID = os.getenv(
    "EDITING_SPREADSHEET_ID", "1_DRIpyvWsVcMT8I0wt0kO8XFRY8UzySSF_qDzMQwO7Q",
).strip()
# Range with no tab name reads the FIRST sheet, so renaming the tab can't
# break the loader.
EDITING_RANGE = (os.getenv("EDITING_SHEET_RANGE") or "A:ZZ").strip()

# How long the parsed exercise bank is trusted before the sheet is re-read.
EDITING_CACHE_TTL_SECONDS = 300

# An exercise needs at least this many numbered lines to be served to
# students. Half-written rows in the sheet (a passage typed in, lines not
# filled yet) are held back rather than shipped broken.
MIN_LINES_PER_EXERCISE = 8

# The daily tally + streak are keyed by (user_id, subject, date) and the rest
# of the app writes them under 'Physics'. English credits the SAME bucket on
# purpose: a student has one daily goal and one streak, whatever they revise.
DAILY_TALLY_SUBJECT = "Physics"

# XP/gems/streak come from the Daily Challenge only, exactly like physics:
# the Practice section is unlimited (60 passages, replayable), so paying it
# out would make XP farmable and would make the daily meaningless. Both
# editing modes therefore report rewards of zero unless the attempt was
# launched from the daily.

MODE_EXAM = "exam"
MODE_PRACTICE = "practice"

# Attempt rows are tagged so History / SavedQuizzes / the teacher dashboard
# can tell an editing attempt from a physics quiz.
QUIZ_TYPE_EXAM = "english_exam"
QUIZ_TYPE_PRACTICE = "english_practice"
QUIZ_TYPE_DAILY = "english_daily"      # launched from the Daily Challenge

# Every editing attempt type, for the history / stats / dashboard queries.
QUIZ_TYPES_ALL = (QUIZ_TYPE_EXAM, QUIZ_TYPE_PRACTICE, QUIZ_TYPE_DAILY)

# Full names for the sheet's error codes -- shown to students in review and
# used for the teacher's weak-spot breakdown.
ERROR_CODES: Dict[str, Dict[str, str]] = {
    "SVA":   {"name": "Subject-verb agreement", "hint": "Match the verb to the real subject."},
    "TENSE": {"name": "Tense",                  "hint": "Look for a time marker in the sentence."},
    "VF":    {"name": "Verb form",              "hint": "Check what the verb follows: to, a modal, has/have."},
    "PREP":  {"name": "Preposition",            "hint": "Which preposition does this verb or noun take?"},
    "ART":   {"name": "Article",                "hint": "a / an / the -- listen to the sound that follows."},
    "PRON":  {"name": "Pronoun",                "hint": "Find the noun it refers to, then match it."},
    "NUM":   {"name": "Singular / plural",      "hint": "Is one thing meant, or many?"},
    "DET":   {"name": "Determiner",             "hint": "this/these, that/those, much/many."},
    "WF":    {"name": "Word form",              "hint": "Noun, verb, adjective or adverb?"},
    "CONJ":   {"name": "Conjunction",           "hint": "Does the joining word match the logic?"},
    "RELPRON":{"name": "Relative pronoun",      "hint": "who / which / that / whose -- person or thing?"},
    "QUANT":  {"name": "Quantifier",            "hint": "much/many, few/little -- can you count it?"},
    "COMP":   {"name": "Comparative",           "hint": "Comparing two things, or picking one from many?"},
    "MODAL":  {"name": "Modal verb",            "hint": "can/could, will/would -- does it fit the meaning?"},
    "SP":     {"name": "Spelling",              "hint": "Check the spelling."},
}

TICK = "✓"  # the sheet writes a tick in Incorrect/Correct Word for clean lines

# ============================================================================
# INJECTED DEPENDENCIES
# ============================================================================
# quiz_backend owns the Google client, the DB pool, JWT verification and the
# XP/streak/gem economy. It hands them over via init() so this module stays
# importable on its own (and unit-testable with fakes).

_D: Dict[str, Any] = {}


def init(**deps) -> None:
    """Inject shared helpers from quiz_backend. Called once at import time."""
    _D.update(deps)


def _dep(name: str):
    fn = _D.get(name)
    if fn is None:
        raise HTTPException(status_code=503, detail=f"English module not initialised ({name})")
    return fn


# ============================================================================
# TEXT NORMALISATION + GRADING PRIMITIVES
# ============================================================================

# Curly quotes and dashes get normalised so a student typing a straight
# apostrophe still matches a sheet answer typed with a curly one.
_PUNCT_MAP = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-",
}

# Stripped from the edges of a token before comparison, never from inside it
# (so "don't" and "well-known" survive intact).
_EDGE_PUNCT = " \t\r\n.,;:!?()[]{}\"'`*_"


def _norm(s: Optional[str]) -> str:
    """Lowercase, de-curl, collapse whitespace, strip edge punctuation."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    for bad, good in _PUNCT_MAP.items():
        s = s.replace(bad, good)
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(_EDGE_PUNCT).lower()


def _is_tick(value: Optional[str]) -> bool:
    """True when a sheet cell means 'this line is correct as printed'.

    Deliberately strict. An earlier version also accepted friendly words
    like "none", "correct" and "ok" -- but those are real answers in this
    bank ("none" -> "no", "corrected" -> "correct"), so three exercises
    quietly lost an error line to a false tick. Only an actual tick mark,
    or an unmistakable phrase, counts.
    """
    if value is None:
        return False
    v = unicodedata.normalize("NFKC", str(value)).strip()
    if not v:
        return False
    return v in ("\u2713", "\u2714") or v.lower() in ("tick", "no error")


def _accepted_answers(correct_word: str) -> List[str]:
    """Split a sheet answer into the alternatives it allows.

    The sheet writes genuine alternatives with a slash -- "from/against",
    "in/at" -- and each side is marked right. A slash inside a single token
    that is really one answer (rare) still splits, which only ever makes
    marking more generous, never less.
    """
    raw = unicodedata.normalize("NFKC", correct_word or "")
    for bad, good in _PUNCT_MAP.items():
        raw = raw.replace(bad, good)
    parts = re.split(r"\s*[/|]\s*|\s+or\s+", raw, flags=re.IGNORECASE)
    out, seen = [], set()
    for p in parts:
        n = _norm(p)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


# A "token" is a run of word characters plus internal apostrophes/hyphens.
# Everything between tokens (spaces, punctuation) is preserved separately so
# the frontend can re-render the line exactly as printed while still making
# each word individually tappable.
_TOKEN_RE = re.compile(r"[A-Za-z0-9’'\-]+")


def tokenize_line(text: str) -> List[Dict[str, Any]]:
    """Split a line into tappable word tokens + the glue between them.

    Returns [{index, word, before, after}] where `index` is the token's
    position among words only -- the id the frontend sends back when a
    student taps a word.
    """
    text = unicodedata.normalize("NFKC", text or "")
    tokens: List[Dict[str, Any]] = []
    cursor = 0
    for i, m in enumerate(_TOKEN_RE.finditer(text)):
        tokens.append({
            "index": i,
            "word": m.group(0),
            "before": text[cursor:m.start()],
            "after": "",
        })
        cursor = m.end()
    if tokens:
        tokens[-1]["after"] = text[cursor:]
    elif text:
        tokens.append({"index": 0, "word": "", "before": text, "after": ""})
    return tokens


def expected_token_indices(line_text: str, incorrect_word: str) -> List[int]:
    """Every token position in the line that counts as "found the error".

    Usually a single word. Two cases widen it:

    - The wrong word appears twice on the line. Tapping either occurrence is
      accepted -- the student has identified the word, and penalising the
      wrong copy of an identical word teaches nothing.
    - The error spans more than one word ("considering to make" -> "making").
      Every token in the span is accepted, so tapping any part of the phrase
      counts.
    """
    targets = [
        _norm(w) for w in
        _TOKEN_RE.findall(unicodedata.normalize("NFKC", incorrect_word or ""))
    ]
    targets = [t for t in targets if t]
    if not targets:
        return []

    words = [_norm(t["word"]) for t in tokenize_line(line_text)]
    span = len(targets)
    hits: List[int] = []
    for start in range(len(words) - span + 1):
        if words[start:start + span] == targets:
            hits.extend(range(start, start + span))
    return sorted(set(hits))


# ============================================================================
# DATA MODELS
# ============================================================================

class EditingLineAnswer(BaseModel):
    """The marking key for one numbered line. Never sent to a student."""
    line_no: int
    text: str
    no_error: bool = False
    incorrect_word: str = ""
    correct_word: str = ""
    accepted: List[str] = []
    error_code: str = ""
    explanation: str = ""
    expected_indices: List[int] = []


class EditingExercise(BaseModel):
    """One full exercise, answers included. Server-side only."""
    uid: str
    title: str
    difficulty: str
    passage: str
    intro_line: str = ""       # unnumbered opening line -- always correct
    outro_line: str = ""       # unnumbered closing line -- always correct
    lines: List[EditingLineAnswer] = []

    @property
    def total_marks(self) -> int:
        return len(self.lines)


class SubmittedLine(BaseModel):
    """What the student did on one line."""
    line_no: int
    no_error: bool = False
    word_index: Optional[int] = None   # token they tapped ("circled")
    correction: str = ""               # what they typed in its place


class EditingSubmitRequest(BaseModel):
    uid: str
    mode: str = MODE_EXAM              # 'exam' | 'practice'
    daily: bool = False                # launched from the Daily Challenge
    time_spent_seconds: int = 0
    answers: List[SubmittedLine] = []


class EditingCheckRequest(BaseModel):
    """Practice mode: mark a single line and explain it immediately."""
    uid: str
    line_no: int
    no_error: bool = False
    word_index: Optional[int] = None
    correction: str = ""


# ============================================================================
# SHEET LOADER + CACHE
# ============================================================================

# Header aliases, so renaming a column heading in the sheet to something
# reasonable doesn't silently drop the data.
_COLUMN_ALIASES = {
    "uid":            ("uid", "unique id", "id", "exercise id"),
    "title":          ("exercise", "title", "topic", "passage title"),
    "difficulty":     ("difficulty", "level"),
    "passage":        ("question text", "passage", "text", "question"),
    "line_no":        ("line #", "line no", "line number", "line", "no", "#"),
    "line_text":      ("line text", "text line", "line content"),
    "incorrect_word": ("incorrect word", "wrong word", "error word", "incorrect"),
    "correct_word":   ("correct word", "answer", "correction", "correct"),
    "error_code":     ("error code", "code", "error type", "type"),
    "explanation":    ("explanation", "reason", "why"),
}


def _build_column_map(header_row: List[str]) -> Dict[str, int]:
    """Map our field names to column positions in the sheet."""
    seen = {}
    for idx, raw in enumerate(header_row):
        key = _norm(raw)
        if key and key not in seen:
            seen[key] = idx
    col: Dict[str, int] = {}
    for field, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in seen:
                col[field] = seen[alias]
                break
    return col


def _cell(row: List[str], col: Dict[str, int], field: str) -> str:
    idx = col.get(field)
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


def _split_passage(passage: str, line_texts: List[str]) -> Dict[str, str]:
    """Recover the two unnumbered lines that bracket the numbered ones.

    The sheet holds the full passage once, and separately holds only the
    numbered lines. Because the numbered lines appear in the passage in
    order and unmodified, walking through the passage and consuming each
    one in turn leaves exactly the opening and closing text behind.
    """
    if not passage or not line_texts:
        return {"intro": "", "outro": "", "ok": False}

    # Fast path: the passage is typed as real lines (12 of them -- an opening
    # line, the ten numbered lines, a closing line). When the middle lines
    # match the numbered rows exactly, the two unnumbered lines fall straight
    # out and no searching is needed.
    if "\n" in passage:
        parts = [p.strip() for p in unicodedata.normalize("NFKC", passage).split("\n")]
        parts = [p for p in parts if p]
        if len(parts) == len(line_texts) + 2:
            def squash(x):
                return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", x or "")).strip()
            if [squash(p) for p in parts[1:-1]] == [squash(t) for t in line_texts]:
                return {"intro": parts[0], "outro": parts[-1], "ok": True}

    flat = unicodedata.normalize("NFKC", passage)
    flat = re.sub(r"\s+", " ", flat).strip()

    cursor = 0
    first_start = None
    last_end = None
    for raw in line_texts:
        needle = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", raw or "")).strip()
        if not needle:
            continue
        found = flat.find(needle, cursor)
        if found < 0:
            # A line was edited in one place and not the other -- bail out
            # rather than guess, and let the caller report it.
            return {"intro": "", "outro": "", "ok": False}
        if first_start is None:
            first_start = found
        cursor = found + len(needle)
        last_end = cursor

    intro = flat[:first_start].strip() if first_start is not None else ""
    outro = flat[last_end:].strip() if last_end is not None else ""
    return {"intro": intro, "outro": outro, "ok": True}


class EditingBank:
    """In-memory bank of parsed exercises, refreshed from the sheet on a TTL.

    Mirrors QuestionCache's contract: never raise on a bad sheet, keep
    serving the last good copy, and record why anything was skipped so a
    teacher can see it in /api/english/health.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._exercises: Dict[str, EditingExercise] = {}
        self._order: List[str] = []
        self._loaded_at: float = 0.0
        self._skipped: List[Dict[str, str]] = []
        self._last_error: Optional[str] = None
        self._row_count: int = 0

    # -- loading ------------------------------------------------------------

    def _fetch_rows(self) -> List[List[str]]:
        svc = _dep("get_sheets_service")()
        if svc is None:
            raise RuntimeError("Google Sheets credentials unavailable")
        result = svc.spreadsheets().values().get(
            spreadsheetId=EDITING_SPREADSHEET_ID,
            range=EDITING_RANGE,
        ).execute()
        return result.get("values", []) or []

    def _parse(self, rows: List[List[str]]) -> None:
        skipped: List[Dict[str, str]] = []

        # Find the header row -- it is the first row that mentions a UID
        # column. Anything above it (a title banner, notes) is ignored.
        header_idx = None
        for i, row in enumerate(rows[:20]):
            if any(_norm(c) in _COLUMN_ALIASES["uid"] for c in row):
                header_idx = i
                break
        if header_idx is None:
            raise RuntimeError("No header row with a 'UID' column found")

        col = _build_column_map(rows[header_idx])
        missing = [f for f in ("uid", "line_no", "line_text", "incorrect_word", "correct_word")
                   if f not in col]
        if missing:
            raise RuntimeError(f"Sheet is missing required columns: {', '.join(missing)}")

        # Group rows by UID, preserving sheet order.
        groups: Dict[str, List[List[str]]] = {}
        order: List[str] = []
        for row in rows[header_idx + 1:]:
            uid = _cell(row, col, "uid")
            if not uid:
                continue
            if uid not in groups:
                groups[uid] = []
                order.append(uid)
            groups[uid].append(row)

        exercises: Dict[str, EditingExercise] = {}
        kept_order: List[str] = []

        for uid in order:
            group = groups[uid]
            # Header values may sit on any row of the group; the first
            # non-empty wins. In practice they are all on the first row.
            title = next((_cell(r, col, "title") for r in group if _cell(r, col, "title")), "")
            difficulty = next((_cell(r, col, "difficulty") for r in group if _cell(r, col, "difficulty")), "")
            passage = next((_cell(r, col, "passage") for r in group if _cell(r, col, "passage")), "")
            line_rows = []
            for r in group:
                raw_no = _cell(r, col, "line_no")
                if not raw_no:
                    continue
                try:
                    n = int(float(raw_no))
                except (TypeError, ValueError):
                    continue
                if _cell(r, col, "line_text"):
                    line_rows.append((n, r))
            line_rows.sort(key=lambda pair: pair[0])

            if len(line_rows) < MIN_LINES_PER_EXERCISE:
                skipped.append({
                    "uid": uid,
                    "title": title,
                    "reason": f"only {len(line_rows)} numbered lines "
                              f"(needs {MIN_LINES_PER_EXERCISE})",
                })
                continue

            lines: List[EditingLineAnswer] = []
            problem: Optional[str] = None
            for n, r in line_rows:
                text = _cell(r, col, "line_text")
                incorrect = _cell(r, col, "incorrect_word")
                correct = _cell(r, col, "correct_word")
                code = _cell(r, col, "error_code").upper()
                expl = _cell(r, col, "explanation")

                if not incorrect and not correct:
                    problem = (f"line {n}: no incorrect/correct word given "
                               f"(use a tick for a clean line)")
                    break

                if _is_tick(incorrect) or _is_tick(correct):
                    lines.append(EditingLineAnswer(
                        line_no=n, text=text, no_error=True,
                        explanation=expl or "Correct as printed.",
                    ))
                    continue

                indices = expected_token_indices(text, incorrect)
                if not indices:
                    problem = (f"line {n}: the word '{incorrect}' does not appear "
                               f"in that line's text")
                    break
                accepted = _accepted_answers(correct)
                if not accepted:
                    problem = f"line {n}: no correct word given for '{incorrect}'"
                    break
                lines.append(EditingLineAnswer(
                    line_no=n, text=text, no_error=False,
                    incorrect_word=incorrect, correct_word=correct,
                    accepted=accepted, error_code=code, explanation=expl,
                    expected_indices=indices,
                ))

            if problem:
                skipped.append({"uid": uid, "title": title, "reason": problem})
                continue

            split = _split_passage(passage, [ln.text for ln in lines])
            exercises[uid] = EditingExercise(
                uid=uid,
                title=title or uid,
                difficulty=(difficulty or "Easy").strip().title(),
                passage=passage,
                intro_line=split["intro"],
                outro_line=split["outro"],
                lines=lines,
            )
            kept_order.append(uid)

        self._exercises = exercises
        self._order = kept_order
        self._skipped = skipped
        self._row_count = len(rows)

    def load(self, force: bool = False) -> None:
        with self._lock:
            fresh = (time.time() - self._loaded_at) < EDITING_CACHE_TTL_SECONDS
            if self._exercises and fresh and not force:
                return
            try:
                rows = self._fetch_rows()
                self._parse(rows)
                self._loaded_at = time.time()
                self._last_error = None
                msg = f"Loaded {len(self._exercises)} editing exercises"
                if self._skipped:
                    msg += f" ({len(self._skipped)} skipped -- see /api/english/health)"
                print(f"✏️  {msg}")
            except Exception as exc:
                self._last_error = str(exc)
                print(f"⚠️  Editing bank load failed: {exc}")
                if not self._exercises:
                    # Nothing cached to fall back on -- leave it empty; the
                    # endpoints report the error instead of 500ing blindly.
                    self._loaded_at = 0.0
                else:
                    # Keep serving the last good copy, retry on the next TTL.
                    self._loaded_at = time.time()

    # -- reads --------------------------------------------------------------

    def all(self) -> List[EditingExercise]:
        self.load()
        return [self._exercises[u] for u in self._order if u in self._exercises]

    def get(self, uid: str) -> Optional[EditingExercise]:
        self.load()
        return self._exercises.get(uid)

    def health(self) -> Dict[str, Any]:
        self.load()
        return {
            "spreadsheet_id": EDITING_SPREADSHEET_ID,
            "loaded": bool(self._exercises),
            "exercise_count": len(self._exercises),
            "rows_read": self._row_count,
            "loaded_at": self._loaded_at,
            "age_seconds": round(time.time() - self._loaded_at, 1) if self._loaded_at else None,
            "last_error": self._last_error,
            "skipped": self._skipped,
            "min_lines_required": MIN_LINES_PER_EXERCISE,
        }


bank = EditingBank()


# ============================================================================
# GRADING
# ============================================================================

def grade_line(key: EditingLineAnswer, given: SubmittedLine) -> Dict[str, Any]:
    """Mark one line and explain the result.

    Marking is all-or-nothing on the mark itself (as in the exam), but the
    breakdown records the two sub-skills separately -- did they FIND the
    wrong word, and did they FIX it -- because those need different teaching
    and the split is what makes the class data worth reading.
    """
    said_no_error = bool(given.no_error)
    correction = (given.correction or "").strip()
    tapped = given.word_index

    if key.no_error:
        correct = said_no_error and not correction
        return {
            "line_no": key.line_no,
            "is_correct": correct,
            "expected_no_error": True,
            "word_correct": correct,
            "correction_correct": correct,
            "correct_word": TICK,
            "incorrect_word": TICK,
            "error_code": "",
            "error_name": "",
            "explanation": key.explanation or "Correct as printed.",
            "user_no_error": said_no_error,
            "user_word_index": tapped,
            "user_word": "",
            "user_correction": correction,
            # A tick line missed is nearly always over-correction: the
            # student "found" an error that was not there.
            "miss_type": None if correct else "over_corrected",
        }

    tokens = tokenize_line(key.text)
    user_word = ""
    if tapped is not None and 0 <= tapped < len(tokens):
        user_word = tokens[tapped]["word"]

    word_correct = tapped is not None and tapped in key.expected_indices
    correction_correct = _norm(correction) in key.accepted if correction else False
    is_correct = bool(word_correct and correction_correct)

    if is_correct:
        miss = None
    elif said_no_error or (tapped is None and not correction):
        miss = "missed_error"          # ticked a line that had an error
    elif not word_correct:
        miss = "wrong_word"            # circled the wrong word
    else:
        miss = "wrong_correction"      # right word, wrong fix

    return {
        "line_no": key.line_no,
        "is_correct": is_correct,
        "expected_no_error": False,
        "word_correct": bool(word_correct),
        "correction_correct": bool(correction_correct),
        "correct_word": key.correct_word,
        "incorrect_word": key.incorrect_word,
        "error_code": key.error_code,
        "error_name": ERROR_CODES.get(key.error_code, {}).get("name", key.error_code),
        "explanation": key.explanation,
        "user_no_error": said_no_error,
        "user_word_index": tapped,
        "user_word": user_word,
        "user_correction": correction,
        "miss_type": miss,
    }


# ============================================================================
# STUDENT-FACING PAYLOADS  (answers stripped)
# ============================================================================

INSTRUCTIONS = (
    "Read the passage below. The first and last lines are correct. For each "
    "numbered line, tap the word that is wrong and type the correction. If a "
    "line has no error, tick it."
)


def exercise_for_student(ex: EditingExercise) -> Dict[str, Any]:
    """Everything the player needs and nothing that gives the game away."""
    return {
        "uid": ex.uid,
        "title": ex.title,
        "difficulty": ex.difficulty,
        "instructions": INSTRUCTIONS,
        "intro_line": ex.intro_line,
        "outro_line": ex.outro_line,
        "total_marks": ex.total_marks,
        "lines": [
            {"line_no": ln.line_no, "tokens": tokenize_line(ln.text)}
            for ln in ex.lines
        ],
    }


def _summarise(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Score plus the per-error-code and per-miss-type rollups."""
    total = len(results)
    score = sum(1 for r in results if r["is_correct"])
    by_code: Dict[str, Dict[str, int]] = {}
    for r in results:
        code = r.get("error_code") or ""
        if not code:
            continue
        slot = by_code.setdefault(code, {"correct": 0, "total": 0})
        slot["total"] += 1
        slot["correct"] += int(r["is_correct"])
    misses: Dict[str, int] = {}
    for r in results:
        if r.get("miss_type"):
            misses[r["miss_type"]] = misses.get(r["miss_type"], 0) + 1
    found = [r for r in results if not r["expected_no_error"]]
    return {
        "score": score,
        "total": total,
        "percentage": round(100 * score / total) if total else 0,
        "words_found": sum(1 for r in found if r["word_correct"]),
        "words_total": len(found),
        "by_error_code": [
            {
                "code": c,
                "name": ERROR_CODES.get(c, {}).get("name", c),
                "correct": v["correct"],
                "total": v["total"],
            }
            for c, v in sorted(by_code.items())
        ],
        "miss_types": misses,
    }


def _auth_user_id(authorization: Optional[str]) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No authorization token")
    payload = _dep("verify_jwt_token")(authorization.replace("Bearer ", ""))
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload.get("user_id")


# ============================================================================
# ENDPOINTS -- STUDENT
# ============================================================================

@router.get("/exercises")
def list_exercises(authorization: str = Header(None)):
    """The exercise picker: every playable exercise + this student's best score."""
    user_id = _auth_user_id(authorization)
    exercises = bank.all()
    if not exercises:
        h = bank.health()
        if h["last_error"]:
            raise HTTPException(status_code=503,
                                detail=f"Editing bank unavailable: {h['last_error']}")

    best: Dict[str, Dict[str, Any]] = {}
    try:
        conn = _dep("get_db_connection")()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT questions_data, score, total_questions, percentage, attempted_at "
                "FROM quiz_attempts WHERE user_id = %s AND quiz_type IN (%s, %s, %s) "
                "ORDER BY attempted_at ASC",
                (user_id, *QUIZ_TYPES_ALL),
            )
            for qd, score, total, pct, at in cursor.fetchall():
                try:
                    rows = json.loads(qd) if qd else []
                    uid = rows[0].get("exercise_uid") if rows else None
                except Exception:
                    uid = None
                if not uid:
                    continue
                prev = best.get(uid)
                if prev is None or int(score or 0) > prev["best_score"]:
                    best[uid] = {
                        "best_score": int(score or 0),
                        "best_percentage": int(pct or 0),
                        "total": int(total or 0),
                    }
                best[uid]["attempts"] = (prev or {}).get("attempts", 0) + 1
                best[uid]["last_attempt"] = at.isoformat() if hasattr(at, "isoformat") else str(at)
        finally:
            cursor.close()
            conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        # History is a nice-to-have on this screen -- never block the list.
        print(f"⚠️  Editing history lookup failed (non-fatal): {exc}")

    return {
        "exercises": [
            {
                "uid": ex.uid,
                "title": ex.title,
                "difficulty": ex.difficulty,
                "total_marks": ex.total_marks,
                "preview": (ex.intro_line or ex.passage)[:110],
                "progress": best.get(ex.uid),
            }
            for ex in exercises
        ],
        "count": len(exercises),
        "difficulties": sorted({ex.difficulty for ex in exercises}),
    }


@router.get("/exercises/{uid}")
def get_exercise(uid: str, authorization: str = Header(None)):
    """Load one exercise for playing. Contains no answers."""
    _auth_user_id(authorization)
    ex = bank.get(uid)
    if not ex:
        raise HTTPException(status_code=404, detail=f"No editing exercise {uid!r}")
    return exercise_for_student(ex)


@router.post("/check")
def check_line(request: EditingCheckRequest, authorization: str = Header(None)):
    """Practice mode -- mark one line straight away and explain it."""
    _auth_user_id(authorization)
    ex = bank.get(request.uid)
    if not ex:
        raise HTTPException(status_code=404, detail=f"No editing exercise {request.uid!r}")
    key = next((ln for ln in ex.lines if ln.line_no == request.line_no), None)
    if key is None:
        raise HTTPException(status_code=404, detail=f"No line {request.line_no} in {request.uid}")
    return grade_line(key, SubmittedLine(
        line_no=request.line_no,
        no_error=request.no_error,
        word_index=request.word_index,
        correction=request.correction,
    ))


# ============================================================================
# ENDPOINTS -- SUBMIT (marking + persistence + rewards)
# ============================================================================

def _persist_attempt(cursor, conn, user_id: int, ex: EditingExercise,
                     results: List[Dict[str, Any]], summary: Dict[str, Any],
                     mode: str, time_spent: int,
                     daily: bool = False) -> Optional[int]:
    """Save the attempt into quiz_attempts so it shows up in History and in
    the teacher dashboard alongside physics quizzes.

    Each line is stored in a shape the existing review renderers can read
    (question_text / user_answer / correct_answer / is_correct / explanation)
    with the editing-specific fields carried alongside them.
    """
    rows = []
    for r in results:
        if r["expected_no_error"]:
            correct_answer = "No error"
            user_answer = "No error" if r["user_no_error"] else (
                f"{r['user_word']} -> {r['user_correction']}".strip(" ->") or "(blank)"
            )
        else:
            correct_answer = f"{r['incorrect_word']} -> {r['correct_word']}"
            if r["user_no_error"]:
                user_answer = "No error"
            elif r["user_word"] or r["user_correction"]:
                user_answer = f"{r['user_word']} -> {r['user_correction']}".strip(" ->")
            else:
                user_answer = "(blank)"
        rows.append({
            # Fields the generic review UI already understands
            "uid": f"{ex.uid}-L{r['line_no']}",
            "qno": str(r["line_no"]),
            "subtopic": "English · Editing",
            "difficulty": ex.difficulty,
            "question_text": next((ln.text for ln in ex.lines if ln.line_no == r["line_no"]), ""),
            "options": "",
            "option_type": "TEXT",
            "answer": correct_answer,
            "correct_answer": correct_answer,
            "user_answer": user_answer,
            "is_correct": r["is_correct"],
            "explanation": r["explanation"],
            # Editing-specific detail, used by the editing review screen
            "exercise_uid": ex.uid,
            "exercise_title": ex.title,
            "line_no": r["line_no"],
            "error_code": r["error_code"],
            "error_name": r["error_name"],
            "word_correct": r["word_correct"],
            "correction_correct": r["correction_correct"],
            "miss_type": r["miss_type"],
            "mode": mode,
        })

    if daily:
        quiz_type = QUIZ_TYPE_DAILY
    else:
        quiz_type = QUIZ_TYPE_EXAM if mode == MODE_EXAM else QUIZ_TYPE_PRACTICE
    cursor.execute(
        "INSERT INTO quiz_attempts "
        "(user_id, name, difficulty, subtopic, score, percentage, total_questions, "
        " time_spent_seconds, questions_data, quiz_type) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            user_id,
            f"Editing · {ex.title}",
            ex.difficulty,
            "English · Editing",
            summary["score"],
            summary["percentage"],
            summary["total"],
            max(0, int(time_spent or 0)),
            json.dumps(rows),
            quiz_type,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _award_rewards(cursor, conn, user_id: int, score: int, total: int,
                   difficulty: str) -> Dict[str, Any]:
    """XP, crystals, daily-goal credit and streak -- the same economy the
    daily physics quiz uses, so a day of English counts exactly as much as a
    day of physics. Mirrors the reward block in /api/quiz/submit."""
    out: Dict[str, Any] = {
        "xp_delta": 0, "xp_total": 0, "xp_breakdown": {},
        "gems_delta": 0, "gems_total": 0, "gems_breakdown": {},
        "rank_up": False, "new_rank": None, "daily_progress": None,
    }

    xp_for_quiz = _dep("xp_for_quiz")
    gems_for_quiz = _dep("gems_for_quiz")
    compute_rank = _dep("compute_rank")
    credit_daily = _dep("credit_daily_practice")
    award_streak = _dep("award_streak_day")
    effective_today = _dep("effective_today")
    C = _D.get("constants", {})
    daily_target = C.get("DAILY_CORRECT_TARGET", 10)

    breakdown = xp_for_quiz(score, total, difficulty)
    xp_pre = 0
    try:
        cursor.execute("SELECT xp FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        xp_pre = int(row[0]) if row and row[0] is not None else 0
    except Exception:
        pass

    today = effective_today(user_id)
    streak_awarded = False
    current_streak = None
    longest_streak = None
    freeze_used = False

    try:
        prev_passed, now_passed, today_correct, today_total = credit_daily(
            cursor, conn, user_id, DAILY_TALLY_SUBJECT, today, score, total,
            target=daily_target,
        )
        if now_passed and not prev_passed:
            current_streak, longest_streak, _freezes, freeze_used = award_streak(
                cursor, conn, user_id, today
            )
            streak_awarded = True
        else:
            cursor.execute(
                "SELECT current_streak, longest_streak FROM streaks WHERE user_id = %s",
                (user_id,),
            )
            srow = cursor.fetchone()
            if srow:
                current_streak, longest_streak = srow[0], srow[1]
        out["daily_progress"] = {
            "today_correct": today_correct,
            "today_total": today_total,
            "target": daily_target,
            "passed_today": now_passed,
            "streak_awarded": streak_awarded,
            "freeze_used": freeze_used,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
        }
    except Exception as exc:
        print(f"⚠️  Editing daily credit failed (non-fatal): {exc}")

    xp_daily_goal = C.get("XP_BONUS_DAILY_GOAL", 15) if streak_awarded else 0
    xp_streak_milestone = 0
    every = C.get("XP_BONUS_STREAK_EVERY", 7)
    if streak_awarded and current_streak and every and current_streak % every == 0:
        xp_streak_milestone = C.get("XP_BONUS_STREAK_AMOUNT", 50)
    breakdown.update({"daily_goal": xp_daily_goal, "streak_milestone": xp_streak_milestone})
    xp_delta = breakdown["base"] + breakdown["perfect"] + xp_daily_goal + xp_streak_milestone

    xp_total = xp_pre
    try:
        if xp_delta > 0:
            cursor.execute("UPDATE users SET xp = xp + %s WHERE id = %s", (xp_delta, user_id))
            # Bank the XP on today's row so the daily/weekly leaderboards
            # rank English work the same as physics work.
            cursor.execute(
                "INSERT INTO daily_challenges "
                "(user_id, subject, challenge_date, score, total, percentage, passed, attempts, xp) "
                "VALUES (%s, %s, %s, 0, 0, 0, FALSE, 0, %s) "
                "ON DUPLICATE KEY UPDATE xp = xp + VALUES(xp)",
                (user_id, DAILY_TALLY_SUBJECT, today, xp_delta),
            )
            conn.commit()
        cursor.execute("SELECT xp FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        xp_total = int(row[0]) if row and row[0] is not None else 0
        pre_rank, post_rank = compute_rank(xp_pre), compute_rank(xp_total)
        if post_rank["tier_index"] > pre_rank["tier_index"]:
            out["rank_up"] = True
            out["new_rank"] = post_rank
    except Exception as exc:
        print(f"⚠️  Editing XP award failed (non-fatal): {exc}")

    gems_delta, gems_breakdown = gems_for_quiz(score, out["rank_up"])
    gems_total = 0
    try:
        if gems_delta > 0:
            cursor.execute("UPDATE users SET gems = gems + %s WHERE id = %s", (gems_delta, user_id))
            conn.commit()
        cursor.execute("SELECT gems FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        gems_total = int(row[0]) if row and row[0] is not None else 0
    except Exception as exc:
        print(f"⚠️  Editing gem award failed (non-fatal): {exc}")

    out.update({
        "xp_delta": xp_delta, "xp_total": xp_total, "xp_breakdown": breakdown,
        "gems_delta": gems_delta, "gems_total": gems_total,
        "gems_breakdown": gems_breakdown,
    })
    return out


@router.post("/submit")
def submit_exercise(request: EditingSubmitRequest, authorization: str = Header(None)):
    """Mark a completed exercise, save it, and pay out."""
    user_id = _auth_user_id(authorization)
    ex = bank.get(request.uid)
    if not ex:
        raise HTTPException(status_code=404, detail=f"No editing exercise {request.uid!r}")

    mode = MODE_PRACTICE if request.mode == MODE_PRACTICE else MODE_EXAM
    is_daily = bool(request.daily)
    given = {a.line_no: a for a in request.answers}
    results = [
        grade_line(ln, given.get(ln.line_no, SubmittedLine(line_no=ln.line_no)))
        for ln in ex.lines
    ]
    summary = _summarise(results)

    attempt_id = None
    rewards: Dict[str, Any] = {}
    conn = _dep("get_db_connection")()
    cursor = conn.cursor()
    try:
        try:
            attempt_id = _persist_attempt(cursor, conn, user_id, ex, results,
                                          summary, mode, request.time_spent_seconds,
                                          daily=is_daily)
        except Exception as exc:
            print(f"⚠️  Saving editing attempt failed (non-fatal): {exc}")

        if is_daily:
            rewards = _award_rewards(cursor, conn, user_id, summary["score"],
                                     summary["total"], ex.difficulty)
        else:
            # The Practice section is reward-free in both modes, but still
            # report the balances so the UI stays accurate.
            try:
                cursor.execute("SELECT xp, gems FROM users WHERE id = %s", (user_id,))
                row = cursor.fetchone()
                rewards = {
                    "xp_delta": 0, "xp_total": int(row[0] or 0) if row else 0,
                    "xp_breakdown": {}, "gems_delta": 0,
                    "gems_total": int(row[1] or 0) if row else 0,
                    "gems_breakdown": {}, "rank_up": False, "new_rank": None,
                    "daily_progress": None,
                }
            except Exception:
                rewards = {}
    finally:
        cursor.close()
        conn.close()

    xp_total = rewards.get("xp_total", 0)
    return {
        "success": True,
        "attempt_id": attempt_id,
        "uid": ex.uid,
        "title": ex.title,
        "difficulty": ex.difficulty,
        "mode": mode,
        "rewarded": is_daily,
        "results": results,
        **summary,
        **rewards,
        "progression": _dep("compute_progression")(xp_total),
    }


# ============================================================================
# ENDPOINTS -- STATS, TEACHER, DIAGNOSTICS
# ============================================================================

def _difficulty_multipliers(levels: List[str]) -> Dict[str, float]:
    """What each level multiplies XP by, straight from the scorer.

    One correct answer out of one is the cheapest probe that makes
    xp_for_quiz report its multiplier, and asking it beats copying the table:
    a UI badge that disagrees with the payout is worse than no badge.
    """
    out: Dict[str, float] = {}
    try:
        xp_for_quiz = _dep("xp_for_quiz")
    except Exception:
        return out
    for level in levels:
        try:
            out[level] = float(xp_for_quiz(1, 1, level).get("diff_mult", 1.0))
        except Exception:
            continue
    return out


@router.get("/daily")
def english_daily(difficulty: Optional[str] = None, authorization: str = Header(None)):
    """Today's editing passage for the Daily Challenge.

    One passage is exactly ten marks, which is the daily target, so clearing
    it clears the day. The pick is weighted toward the error codes the
    student gets wrong most — the same idea as the physics daily weighting
    toward weak topics — and seeded on (user, date, difficulty) so it is the
    SAME passage all day however many times they open the screen, while
    still giving a different one per difficulty rather than the same passage
    relabelled.

    `difficulty` narrows the pool the way the physics daily's difficulty
    picker does; omitted (or unrecognised) means the whole bank. It is
    matched case-insensitively so the query string doesn't have to know how
    the sheet capitalises its levels.
    """
    user_id = _auth_user_id(authorization)
    exercises = bank.all()
    if not exercises:
        h = bank.health()
        raise HTTPException(status_code=503,
                            detail=f"Editing bank unavailable: {h['last_error'] or 'no exercises'}")

    effective_today = _dep("effective_today")
    today = effective_today(user_id)

    attempted: set = set()
    code_stats: Dict[str, List[int]] = {}          # code -> [correct, total]
    daily = None

    conn = _dep("get_db_connection")()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT questions_data FROM quiz_attempts "
            "WHERE user_id = %s AND quiz_type IN (%s, %s, %s) "
            "ORDER BY attempted_at DESC LIMIT 200",
            (user_id, *QUIZ_TYPES_ALL),
        )
        for (blob,) in cursor.fetchall():
            try:
                rows = json.loads(blob) if blob else []
            except Exception:
                continue
            if not rows:
                continue
            if rows[0].get("exercise_uid"):
                attempted.add(rows[0]["exercise_uid"])
            for row in rows:
                code = row.get("error_code") or ""
                if not code:
                    continue
                slot = code_stats.setdefault(code, [0, 0])
                slot[1] += 1
                slot[0] += int(bool(row.get("is_correct")))

        # Today's shared tally — English and physics both credit this row.
        cursor.execute(
            "SELECT score, total, passed FROM daily_challenges "
            "WHERE user_id = %s AND subject = %s AND challenge_date = %s",
            (user_id, DAILY_TALLY_SUBJECT, today),
        )
        row = cursor.fetchone()
        if row:
            daily = {"today_correct": int(row[0] or 0),
                     "today_total": int(row[1] or 0),
                     "passed_today": bool(row[2])}
    except Exception as exc:
        print(f"\u26a0\ufe0f  Editing daily lookup failed (non-fatal): {exc}")
    finally:
        cursor.close()
        conn.close()

    target = _D.get("constants", {}).get("DAILY_CORRECT_TARGET", 10)
    if daily is None:
        daily = {"today_correct": 0, "today_total": 0, "passed_today": False}
    daily["target"] = target

    def code_weight(code: str) -> int:
        """Weaker code -> heavier. Unseen codes get moderate coverage, so a
        new student still meets a spread rather than the same few."""
        correct, total = code_stats.get(code, (0, 0))
        if not total:
            return 40
        return max(5, 100 - round(100 * correct / total))

    # Narrow to the chosen level first, so "unseen" and the weighting below
    # both mean "within this difficulty". An unknown level is ignored rather
    # than fatal: a passage at the wrong level beats no daily at all.
    wanted = (difficulty or "").strip().lower()
    levels = sorted({ex.difficulty for ex in exercises})
    at_level = [ex for ex in exercises if ex.difficulty.lower() == wanted] if wanted else []
    in_scope = at_level or exercises

    # Unseen passages first; once they've all been done, the level is back in
    # play rather than the daily running dry.
    pool = [ex for ex in in_scope if ex.uid not in attempted] or in_scope
    weights = [
        max(1, sum(code_weight(ln.error_code) for ln in ex.lines if ln.error_code))
        for ex in pool
    ]

    # Seeded on user + date + level: the same passage all day, a different
    # one tomorrow, not the same passage for everyone, and switching level
    # gives a genuinely different passage rather than a reshuffle.
    rng = random.Random(f"{user_id}-{today.isoformat()}-{wanted}")
    chosen = rng.choices(pool, weights=weights, k=1)[0]

    weakest = sorted(
        ({"code": c, "name": ERROR_CODES.get(c, {}).get("name", c),
          "accuracy": round(100 * v[0] / v[1])} for c, v in code_stats.items() if v[1]),
        key=lambda x: x["accuracy"],
    )[:2]

    return {
        "uid": chosen.uid,
        "title": chosen.title,
        "difficulty": chosen.difficulty,
        "total_marks": chosen.total_marks,
        "already_attempted": chosen.uid in attempted,
        "daily_progress": daily,
        "focus": weakest,            # what this pick is aimed at, for the UI
        "difficulties": levels,      # what the picker may offer
        # Asked of the scorer itself rather than restated here, so the badge
        # on the picker can't drift away from what the level actually pays.
        "difficulty_multipliers": _difficulty_multipliers(levels),
        # What was actually honoured — the UI says so when a level had
        # nothing in it and the pick fell back to the whole bank.
        "requested_difficulty": difficulty or None,
        "difficulty_applied": bool(at_level),
    }


@router.get("/stats")
def my_stats(authorization: str = Header(None)):
    """This student's editing record, broken down by error type."""
    user_id = _auth_user_id(authorization)
    conn = _dep("get_db_connection")()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT questions_data FROM quiz_attempts "
            "WHERE user_id = %s AND quiz_type IN (%s, %s, %s) ORDER BY attempted_at DESC LIMIT 200",
            (user_id, *QUIZ_TYPES_ALL),
        )
        by_code: Dict[str, Dict[str, int]] = {}
        misses: Dict[str, int] = {}
        attempts = 0
        marks = correct = 0
        for (qd,) in cursor.fetchall():
            try:
                rows = json.loads(qd) if qd else []
            except Exception:
                continue
            if not rows:
                continue
            attempts += 1
            for r in rows:
                marks += 1
                correct += int(bool(r.get("is_correct")))
                code = r.get("error_code") or ""
                if code:
                    slot = by_code.setdefault(code, {"correct": 0, "total": 0})
                    slot["total"] += 1
                    slot["correct"] += int(bool(r.get("is_correct")))
                if r.get("miss_type"):
                    misses[r["miss_type"]] = misses.get(r["miss_type"], 0) + 1
    finally:
        cursor.close()
        conn.close()

    codes = [
        {
            "code": c,
            "name": ERROR_CODES.get(c, {}).get("name", c),
            "hint": ERROR_CODES.get(c, {}).get("hint", ""),
            "correct": v["correct"],
            "total": v["total"],
            "accuracy": round(100 * v["correct"] / v["total"]) if v["total"] else 0,
        }
        for c, v in by_code.items()
    ]
    codes.sort(key=lambda x: (x["accuracy"], -x["total"]))
    return {
        "attempts": attempts,
        "marks": marks,
        "correct": correct,
        "accuracy": round(100 * correct / marks) if marks else 0,
        "by_error_code": codes,
        "weakest": codes[:3],
        "miss_types": misses,
    }


@router.get("/teacher/overview")
def teacher_overview(days: int = 30, authorization: str = Header(None)):
    """Class-wide editing performance, worst error type first."""
    _dep("require_teacher")(authorization)
    conn = _dep("get_db_connection")()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT qa.user_id, u.name, qa.questions_data, qa.score, qa.total_questions, "
            "       qa.percentage, qa.attempted_at "
            "FROM quiz_attempts qa JOIN users u ON u.id = qa.user_id "
            "WHERE qa.quiz_type IN (%s, %s, %s) "
            "  AND qa.attempted_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
            "ORDER BY qa.attempted_at DESC",
            (*QUIZ_TYPES_ALL, max(1, int(days))),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    by_code: Dict[str, Dict[str, Any]] = {}
    by_exercise: Dict[str, Dict[str, Any]] = {}
    students: Dict[int, Dict[str, Any]] = {}
    misses: Dict[str, int] = {}

    for user_id, name, qd, score, total, pct, at in rows:
        try:
            lines = json.loads(qd) if qd else []
        except Exception:
            continue
        if not lines:
            continue

        s = students.setdefault(user_id, {
            "user_id": user_id, "name": name or "Student",
            "attempts": 0, "marks": 0, "correct": 0,
        })
        s["attempts"] += 1
        s["marks"] += len(lines)
        s["correct"] += sum(1 for l in lines if l.get("is_correct"))

        title = lines[0].get("exercise_title") or lines[0].get("exercise_uid") or "Untitled"
        ex = by_exercise.setdefault(title, {"title": title, "attempts": 0, "score": 0, "marks": 0})
        ex["attempts"] += 1
        ex["score"] += int(score or 0)
        ex["marks"] += int(total or 0)

        for l in lines:
            code = l.get("error_code") or ""
            if code:
                slot = by_code.setdefault(code, {"correct": 0, "total": 0, "strugglers": set()})
                slot["total"] += 1
                if l.get("is_correct"):
                    slot["correct"] += 1
                else:
                    slot["strugglers"].add(name or "Student")
            if l.get("miss_type"):
                misses[l["miss_type"]] = misses.get(l["miss_type"], 0) + 1

    codes = [
        {
            "code": c,
            "name": ERROR_CODES.get(c, {}).get("name", c),
            "hint": ERROR_CODES.get(c, {}).get("hint", ""),
            "correct": v["correct"],
            "total": v["total"],
            "accuracy": round(100 * v["correct"] / v["total"]) if v["total"] else 0,
            "strugglers": sorted(v["strugglers"])[:12],
            "struggler_count": len(v["strugglers"]),
        }
        for c, v in by_code.items()
    ]
    codes.sort(key=lambda x: (x["accuracy"], -x["total"]))

    for s in students.values():
        s["accuracy"] = round(100 * s["correct"] / s["marks"]) if s["marks"] else 0
    roster = sorted(students.values(), key=lambda s: s["accuracy"])

    exercises = [
        {**e, "average": round(100 * e["score"] / e["marks"]) if e["marks"] else 0}
        for e in by_exercise.values()
    ]
    exercises.sort(key=lambda e: e["average"])

    return {
        "window_days": days,
        "attempt_count": len(rows),
        "student_count": len(students),
        "weakest_error_codes": codes[:6],
        "by_error_code": codes,
        "by_exercise": exercises,
        "students": roster,
        "miss_types": misses,
    }


@router.get("/health")
def health(authorization: str = Header(None)):
    """Loader diagnostics -- which exercises loaded, which were held back and why."""
    _dep("require_teacher")(authorization)
    return bank.health()


@router.post("/refresh")
def refresh(authorization: str = Header(None)):
    """Re-read the sheet right now, without waiting for the TTL."""
    _dep("require_teacher")(authorization)
    bank.load(force=True)
    h = bank.health()
    return {"success": h["last_error"] is None, **h}
