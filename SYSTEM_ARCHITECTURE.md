# System Architecture - Option Types Flow

## 🔄 Complete System Flow

### From Google Sheet → Backend → Frontend → Student

```
┌─────────────────────────────────────────────────────────────────┐
│                    GOOGLE SHEET (Paper1)                        │
│                                                                  │
│  UID    | QNo | Question | Options      | Answer | Diagram     │
│─────────┼─────┼──────────┼──────────────┼────────┼─────────────│
│ PHY-001 | Q1  | ...      | A) text...   | B      | image.png   │
│ PHY-002 | Q2  | ...      | IMAGE:       | C      | options.png │
│ PHY-003 | Q3  | ...      | TABLE:...    | A      | diagram.png │
└─────────────────────────────────────────────────────────────────┘
                           ↓
                    (Sheets API)
                           ↓
┌─────────────────────────────────────────────────────────────────┐
│              BACKEND (Python FastAPI)                           │
│                                                                  │
│  1. Load questions from sheet                                   │
│  2. For each question:                                          │
│     • Parse options string                                      │
│     • Detect type: TEXT, TABLE, or IMAGE                       │
│     • Parse accordingly                                         │
│     • Get image URL from Drive                                  │
│  3. Cache in memory                                             │
│  4. Return via REST API                                         │
└─────────────────────────────────────────────────────────────────┘
                           ↓
                      REST API
                  (JSON Response)
                           ↓
┌─────────────────────────────────────────────────────────────────┐
│            FRONTEND (React)                                     │
│                                                                  │
│  1. Fetch quiz from backend                                     │
│  2. For each question:                                          │
│     • Check option_type field                                   │
│     • Call appropriate render function:                         │
│       - renderTextOptions()                                     │
│       - renderTableOptions()                                    │
│       - renderImageOptions()                                    │
│  3. Display options to student                                  │
│  4. Collect answer                                              │
│  5. Grade and show results                                      │
└─────────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────────┐
│                STUDENT SEES                                     │
│                                                                  │
│  Option Type │ Rendering                                        │
│──────────────┼────────────────────────────────────────────────  │
│  TEXT        │ ○ A) Option text                                │
│              │ ○ B) Option text                                │
│              │ ○ C) Option text                                │
│              │ ○ D) Option text                                │
│                                                                  │
│  TABLE       │ ┌─────────────────────────────┐                │
│              │ │ Header1 │ Header2 │ Header3 │                │
│              │ ├─────────┼─────────┼─────────┤                │
│              │ │ ○ A) val1 │ val2 │ val3 │                 │
│              │ │ ○ B) val4 │ val5 │ val6 │                 │
│              │ │ ○ C) val7 │ val8 │ val9 │                 │
│              │ │ ○ D) val10│val11 │val12 │                 │
│              │ └─────────────────────────────┘                │
│                                                                  │
│  IMAGE       │ ┌──────────────────┐                            │
│              │ │  [Diagram Image] │                            │
│              │ │  (4 options)     │                            │
│              │ └──────────────────┘                            │
│              │ ○ A) Option A                                   │
│              │ ○ B) Option B                                   │
│              │ ○ C) Option C                                   │
│              │ ○ D) Option D                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📊 Data Flow Detail

### Step 1: Google Sheet → Backend Parsing

**TEXT Format:**
```
Google Sheet (Options column):
A) moving with acceleration
B) moving with constant speed
C) stationary
D) moving backward

↓ parse_option_type()

Backend (Question object):
option_type = "TEXT"
options = "A) moving with acceleration\nB) moving with constant speed\nC) stationary\nD) moving backward"
table_headers = None
table_rows = None
```

**TABLE Format:**
```
Google Sheet (Options column):
TABLE:
Property | Change
A) mass | no change
B) weight | decreases
C) density | increases
D) volume | doubles

↓ parse_option_type()

Backend (Question object):
option_type = "TABLE"
options = "TABLE:\nProperty | Change\nA) mass | no change\n..."
table_headers = ["Property", "Change"]
table_rows = [
  {"_letter": "A", "Property": "mass", "Change": "no change"},
  {"_letter": "B", "Property": "weight", "Change": "decreases"},
  {"_letter": "C", "Property": "density", "Change": "increases"},
  {"_letter": "D", "Property": "volume", "Change": "doubles"}
]
```

**IMAGE Format:**
```
Google Sheet (Options column):
IMAGE:

Google Sheet (Diagram column):
10TtAVgxTsczSFxIrkwSSy_KFQlebWCiX/PHY-002

↓ parse_option_type() + get_image_url()

Backend (Question object):
option_type = "IMAGE"
options = "IMAGE:"
table_headers = None
table_rows = None
image_url = "https://drive.google.com/uc?id=...export=download"
```

### Step 2: Backend → Frontend (REST API)

**GET /api/quiz (POST request)**

Request:
```json
{
  "difficulty": "Easy",
  "subtopic": "Kinematics",
  "count": 5
}
```

Response:
```json
{
  "questions": [
    {
      "uid": "PHY-001",
      "qno": "Q1",
      "subtopic": "Kinematics",
      "difficulty": "Easy",
      "question_text": "A ball moves horizontally...",
      "options": "A) constant\nB) changing\nC) stationary\nD) circular",
      "answer": "B",
      "image_url": "https://drive.google.com/...",
      "option_type": "TEXT",
      "table_headers": null,
      "table_rows": null
    },
    {
      "uid": "PHY-005",
      "qno": "Q5",
      "subtopic": "Forces",
      "difficulty": "Medium",
      "question_text": "Compare mass and weight...",
      "options": "TABLE:\nProperty | Change\nA) mass | no change\n...",
      "answer": "A",
      "image_url": null,
      "option_type": "TABLE",
      "table_headers": ["Property", "Change"],
      "table_rows": [
        {"_letter": "A", "Property": "mass", "Change": "no change"},
        {"_letter": "B", "Property": "weight", "Change": "decreases"},
        ...
      ]
    },
    {
      "uid": "PHY-002",
      "qno": "Q2",
      "subtopic": "Optics",
      "difficulty": "Hard",
      "question_text": "Which shows correct refraction?",
      "options": "IMAGE:",
      "answer": "C",
      "image_url": "https://drive.google.com/...",
      "option_type": "IMAGE",
      "table_headers": null,
      "table_rows": null
    }
  ],
  "count": 3,
  "filters": {
    "difficulty": "Easy",
    "subtopic": "Kinematics"
  }
}
```

### Step 3: Frontend → Student Display

**React Component Decision Tree:**

```
Question received from API
        ↓
   Check option_type
   /        |        \
  /         |         \
TEXT      TABLE      IMAGE
  │         │          │
  ├─→ ├─→  ├─→
  │   │    │
[Radio] [Table] [Image]
[Buttons] [Rows] [+Buttons]
```

**For TEXT:**
```javascript
renderTextOptions()
↓
Split options by newline
Extract letter (A/B/C/D) and text
Create radio button for each
```

**For TABLE:**
```javascript
renderTableOptions()
↓
Extract table_headers from API response
Extract table_rows from API response
Build HTML table with:
  - Header row
  - Radio buttons in first column
  - Data in other columns
```

**For IMAGE:**
```javascript
renderImageOptions()
↓
Display image_url
Below image, create 4 radio buttons:
  - Option A
  - Option B
  - Option C
  - Option D
```

---

## 🔀 CSS Rendering Path

```
QuizMaker.jsx
    ↓
renderOptions()
    ↓
    ├─ option_type === "TEXT"
    │  ↓
    │  <div className="options-container">
    │    └─ Multiple <label className="option-label">
    │
    ├─ option_type === "TABLE"
    │  ↓
    │  <div className="table-options-container">
    │    └─ <table className="options-table">
    │       ├─ <thead>
    │       └─ <tbody>
    │
    └─ option_type === "IMAGE"
       ↓
       <div className="image-options-container">
         ├─ <img> (diagram)
         └─ <div className="options-container">
            └─ 4x <label className="option-label">
```

---

## 🗄️ Data Model Changes

### Before (TEXT only)
```
Question {
  uid: str
  qno: str
  subtopic: str
  difficulty: str
  question_text: str
  options: str           ← "A) text\nB) text\n..."
  answer: str           ← "A"
  image_url: str | None ← "https://..."
}
```

### After (All types)
```
Question {
  uid: str
  qno: str
  subtopic: str
  difficulty: str
  question_text: str
  options: str                    ← Can be TEXT, TABLE:, or IMAGE:
  answer: str                     ← Always just "A", "B", "C", or "D"
  image_url: str | None           ← Setup image (for TEXT/TABLE) or options image (for IMAGE)
  
  NEW FIELDS:
  option_type: str               ← "TEXT" | "TABLE" | "IMAGE"
  table_headers: List[str] | None ← ["Header1", "Header2", ...] or None
  table_rows: List[dict] | None   ← [{"_letter": "A", "Header1": "val1", ...}, ...] or None
}
```

---

## 🔐 Answer Validation

All option types validate the same way:

```
User selects: Option B
↓
Frontend extracts: Letter "B"
↓
Submit quiz
↓
Backend compares:
  user_answer = "B"
  question.answer = "B"
  Match? → Correct ✓
  No match? → Incorrect ✗
```

**Answer format is always the same: Single letter (A, B, C, or D)**

---

## 📈 Performance

### Backend Performance
- **Question Loading**: O(n) where n = number of questions
  - Parses each question once at startup (cached)
  - TABLE parsing: O(headers + rows)
  - No performance hit during quiz taking

- **API Response**: Same structure whether TEXT/TABLE/IMAGE
  - JSON size: ~1KB per question
  - Network: Not affected by option type

### Frontend Performance
- **Rendering**: O(n) where n = options per question (always 4)
  - TEXT: 4 radio buttons
  - TABLE: 1 table with 4 rows
  - IMAGE: 1 image + 4 buttons

- **Memory**: Negligible
  - CSS: +~2KB for new styles
  - JS: No new objects, just conditional rendering

---

## 🧪 Testing Flow

```
1. Update Google Sheet
        ↓
2. Start Backend: python quiz_backend.py
        ↓
   Logs:
   ✅ Loaded 29 questions
   📊 PHY-001: TEXT
   📊 PHY-002: IMAGE
   📊 PHY-003: TABLE
        ↓
3. Start Frontend: npm run dev
        ↓
4. Create Quiz
        ↓
5. For each question type:
   ├─ TEXT: See radio buttons ✓
   ├─ TABLE: See HTML table ✓
   └─ IMAGE: See image + buttons ✓
        ↓
6. Answer questions
        ↓
7. Submit quiz
        ↓
8. See results with grading
```

---

## 🎯 System Benefits

| Aspect | Benefit |
|--------|---------|
| **Flexibility** | Support different question formats |
| **Clarity** | Table format shows data clearly |
| **Visual** | Diagram-based questions more intuitive |
| **Backward Compatible** | Existing questions work unchanged |
| **Performance** | Minimal overhead, cached parsing |
| **User Experience** | Responsive rendering on mobile |

---

## 🚀 Deployment Checklist

- [x] Backend parsing logic implemented
- [x] Frontend rendering logic implemented
- [x] CSS styling added
- [x] No breaking changes (backward compatible)
- [ ] Google Sheet updated with markers (YOUR TASK!)
- [ ] Test with sample questions
- [ ] Verify grading works
- [ ] Deploy to production

---

**Ready to see it in action? Start with your Google Sheet updates!** 🎉
