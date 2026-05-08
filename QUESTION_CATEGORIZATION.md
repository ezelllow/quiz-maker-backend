# Question Categorization System

This guide explains how to categorize questions by type so that Claude Code and other tools can easily extract and work with them.

---

## 🎯 Overview

Every question is automatically categorized into one of three types:

1. **TEXT** - Traditional A/B/C/D multiple choice
2. **TABLE** - Options formatted as structured table
3. **IMAGE** - Options shown in diagram image

This categorization happens automatically when questions are loaded from your Google Sheet.

---

## 📋 What Gets Categorized

When a question is loaded, it's analyzed for:

```python
class QuestionCategory(BaseModel):
    type: str                    # "TEXT", "TABLE", or "IMAGE"
    subtopic: str               # From sheet
    difficulty: str             # From sheet
    has_setup_image: bool       # Whether Diagram column has image
    table_column_count: int     # For TABLE type only
    description: str            # Human-readable category
```

---

## 🔍 Automatic Detection

The system automatically detects type by looking at the `Options` column:

```
"A) text"           → TEXT (default)
"TABLE: ..."        → TABLE
"IMAGE:"            → IMAGE
```

**No manual categorization needed!** It happens automatically.

---

## 📡 New API Endpoints for Claude Code

### 1. Get Questions by Category

**Endpoint:** `GET /api/questions/by-category`

**Returns:** Questions grouped by type

```bash
curl http://localhost:8000/api/questions/by-category
```

**Response:**
```json
{
  "TEXT": [
    {
      "uid": "PHY-001",
      "qno": "Q1",
      "subtopic": "Kinematics",
      "difficulty": "Easy",
      "option_type": "TEXT",
      ...
    }
  ],
  "TABLE": [
    {
      "uid": "PHY-005",
      "qno": "Q5",
      "subtopic": "Forces",
      "difficulty": "Medium",
      "option_type": "TABLE",
      "table_headers": ["Property", "Change"],
      "table_rows": [...]
      ...
    }
  ],
  "IMAGE": [
    {
      "uid": "PHY-002",
      "qno": "Q2",
      "subtopic": "Optics",
      "difficulty": "Hard",
      "option_type": "IMAGE",
      "image_url": "https://...",
      ...
    }
  ]
}
```

### 2. Get Questions by Type Only

**Endpoint:** `GET /api/questions/type/{type}`

**Examples:**
```bash
curl http://localhost:8000/api/questions/type/TEXT
curl http://localhost:8000/api/questions/type/TABLE
curl http://localhost:8000/api/questions/type/IMAGE
```

**Response:**
```json
{
  "type": "TABLE",
  "count": 12,
  "questions": [
    {
      "uid": "...",
      "option_type": "TABLE",
      "table_headers": [...],
      "table_rows": [...]
    }
  ]
}
```

### 3. Get Category Statistics

**Endpoint:** `GET /api/statistics/categories`

**Shows:** How many questions of each type

```bash
curl http://localhost:8000/api/statistics/categories
```

**Response:**
```json
{
  "TEXT": {
    "count": 18,
    "percentage": 62.1,
    "by_difficulty": {
      "Easy": 8,
      "Medium": 7,
      "Hard": 3
    },
    "by_subtopic": {
      "Kinematics": 5,
      "Forces": 4,
      "Energy": 3,
      ...
    }
  },
  "TABLE": {
    "count": 7,
    "percentage": 24.1,
    "by_difficulty": {...},
    "by_subtopic": {...}
  },
  "IMAGE": {
    "count": 4,
    "percentage": 13.8,
    "by_difficulty": {...},
    "by_subtopic": {...}
  }
}
```

### 4. Export Questions by Category

**Endpoint:** `GET /api/export/questions-by-category`

**Format:** Returns data suitable for Claude Code to process

```bash
curl http://localhost:8000/api/export/questions-by-category > categories.json
```

---

## 💾 Backend Implementation

Add these utilities to `quiz_backend.py`:

```python
from collections import defaultdict
from pydantic import BaseModel

class QuestionStats(BaseModel):
    type: str
    count: int
    percentage: float
    by_difficulty: dict
    by_subtopic: dict
    table_columns: int = None  # For TABLE type

def categorize_all_questions() -> dict:
    """Get all questions organized by type"""
    if not cache.is_loaded:
        cache.load_questions()
    
    categorized = {
        'TEXT': [],
        'TABLE': [],
        'IMAGE': []
    }
    
    for question in cache.questions:
        # Skip setup rows
        if question.subtopic.lower() == 'question setup':
            continue
        
        categorized[question.option_type].append(question)
    
    return categorized

def get_category_statistics() -> dict:
    """Get statistics on question categories"""
    categorized = categorize_all_questions()
    total = sum(len(q) for q in categorized.values())
    
    stats = {}
    for qtype, questions in categorized.items():
        by_difficulty = defaultdict(int)
        by_subtopic = defaultdict(int)
        
        for q in questions:
            by_difficulty[q.difficulty] += 1
            by_subtopic[q.subtopic] += 1
        
        stats[qtype] = {
            'count': len(questions),
            'percentage': round((len(questions) / total * 100), 1) if total > 0 else 0,
            'by_difficulty': dict(by_difficulty),
            'by_subtopic': dict(by_subtopic)
        }
    
    return stats

@app.get("/api/questions/by-category")
async def get_questions_by_category():
    """Get all questions organized by type"""
    try:
        categorized = categorize_all_questions()
        return categorized
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/questions/type/{qtype}")
async def get_questions_by_type(qtype: str):
    """Get questions of specific type"""
    qtype = qtype.upper()
    if qtype not in ['TEXT', 'TABLE', 'IMAGE']:
        raise HTTPException(status_code=400, detail=f"Invalid type: {qtype}")
    
    try:
        categorized = categorize_all_questions()
        questions = categorized.get(qtype, [])
        
        return {
            'type': qtype,
            'count': len(questions),
            'questions': questions
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/statistics/categories")
async def get_category_statistics_endpoint():
    """Get statistics on question categories"""
    try:
        stats = get_category_statistics()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

---

## 🔧 How to Use for Claude Code

### Example 1: Extract All TABLE Questions

**Claude Code Script:**
```python
import requests

# Get all questions by category
response = requests.get('http://localhost:8000/api/questions/by-category')
data = response.json()

# Get TABLE questions
table_questions = data['TABLE']

# Process them
for question in table_questions:
    print(f"UID: {question['uid']}")
    print(f"Subtopic: {question['subtopic']}")
    print(f"Headers: {question['table_headers']}")
    print(f"Rows: {question['table_rows']}")
    print("---")
```

### Example 2: Get Statistics

**Claude Code Script:**
```python
import requests

# Get category statistics
response = requests.get('http://localhost:8000/api/statistics/categories')
stats = response.json()

# Show breakdown
for qtype, data in stats.items():
    print(f"{qtype}: {data['count']} questions ({data['percentage']}%)")
    print(f"  By difficulty: {data['by_difficulty']}")
    print(f"  By subtopic: {data['by_subtopic']}")
```

### Example 3: Filter by Type and Difficulty

**Claude Code Script:**
```python
import requests

# Get TEXT questions only
response = requests.get('http://localhost:8000/api/questions/type/TEXT')
data = response.json()

questions = data['questions']

# Filter by difficulty
easy_questions = [q for q in questions if q['difficulty'].lower() == 'easy']
medium_questions = [q for q in questions if q['difficulty'].lower() == 'medium']
hard_questions = [q for q in questions if q['difficulty'].lower() == 'hard']

print(f"Easy: {len(easy_questions)}")
print(f"Medium: {len(medium_questions)}")
print(f"Hard: {len(hard_questions)}")
```

---

## 📊 Question Structure by Type

### TEXT Question (Categorized)
```json
{
  "uid": "PHY-001",
  "qno": "Q1",
  "subtopic": "Kinematics",
  "difficulty": "Easy",
  "question_text": "A ball moves...",
  "options": "A) constant\nB) changing\nC) stationary\nD) circular",
  "answer": "B",
  "option_type": "TEXT",
  "table_headers": null,
  "table_rows": null,
  "image_url": "https://..." or null
}
```

### TABLE Question (Categorized)
```json
{
  "uid": "PHY-005",
  "qno": "Q5",
  "subtopic": "Forces",
  "difficulty": "Medium",
  "question_text": "Compare mass and weight...",
  "options": "TABLE:\nProperty | Change\nA) mass | no change\n...",
  "answer": "A",
  "option_type": "TABLE",
  "table_headers": ["Property", "Change"],
  "table_rows": [
    {"_letter": "A", "Property": "mass", "Change": "no change"},
    {"_letter": "B", "Property": "weight", "Change": "decreases"},
    ...
  ],
  "image_url": null
}
```

### IMAGE Question (Categorized)
```json
{
  "uid": "PHY-002",
  "qno": "Q2",
  "subtopic": "Optics",
  "difficulty": "Hard",
  "question_text": "Which shows correct refraction?",
  "options": "IMAGE:",
  "answer": "C",
  "option_type": "IMAGE",
  "table_headers": null,
  "table_rows": null,
  "image_url": "https://drive.google.com/..."
}
```

---

## 🎯 Common Claude Code Tasks

### Task 1: Generate quiz from specific question types

```python
# Get only IMAGE questions for visual quiz
response = requests.get('http://localhost:8000/api/questions/type/IMAGE')
image_questions = response.json()['questions']

# Create quiz from these questions
quiz_data = {
    'questions': image_questions,
    'title': 'Visual Diagram Quiz',
    'type': 'IMAGE'
}
```

### Task 2: Create practice sets by difficulty

```python
# Get all questions
response = requests.get('http://localhost:8000/api/questions/by-category')
all_questions = []
for qtype, questions in response.json().items():
    all_questions.extend(questions)

# Group by difficulty
by_difficulty = {}
for q in all_questions:
    difficulty = q['difficulty']
    if difficulty not in by_difficulty:
        by_difficulty[difficulty] = []
    by_difficulty[difficulty].append(q)

# Create practice sets
for difficulty, questions in by_difficulty.items():
    print(f"{difficulty}: {len(questions)} questions")
```

### Task 3: Analyze question distribution

```python
import requests

# Get statistics
response = requests.get('http://localhost:8000/api/statistics/categories')
stats = response.json()

# Analyze
print("Question Type Distribution:")
for qtype, data in stats.items():
    print(f"  {qtype}: {data['count']} ({data['percentage']}%)")

# Most common subtopics by type
for qtype, data in stats.items():
    subtopics = data['by_subtopic']
    most_common = max(subtopics.items(), key=lambda x: x[1])
    print(f"  {qtype} most common: {most_common[0]} ({most_common[1]})")
```

---

## 🔑 Key Points for Claude Code

### Automatic Categorization
✅ No manual marking needed
✅ Happens when questions are loaded
✅ Based on `Options` column format

### Easy Access
✅ Dedicated API endpoints
✅ Organized by type
✅ Statistics available

### Consistent Structure
✅ All TEXT questions same structure
✅ All TABLE questions same structure
✅ All IMAGE questions same structure

### Extensible
✅ Can add more filters (by subtopic, difficulty)
✅ Can add custom categories
✅ Can add more metadata fields

---

## 🚀 Quick Examples

**Get count of each type:**
```bash
curl http://localhost:8000/api/statistics/categories | python -m json.tool
```

**Extract all IMAGE UIDs:**
```bash
curl http://localhost:8000/api/questions/type/IMAGE | \
  python -c "import sys, json; questions = json.load(sys.stdin)['questions']; print('\n'.join([q['uid'] for q in questions]))"
```

**Get questions for a specific subtopic:**
```python
import requests
response = requests.get('http://localhost:8000/api/questions/by-category')
all_questions = []
for qtype, questions in response.json().items():
    all_questions.extend(questions)

kinematics = [q for q in all_questions if q['subtopic'] == 'Kinematics']
```

---

## 📚 Integration with Claude Code Projects

### For HabitGo (Practice Simulator)
```python
# Load questions by type for practice
TEXT_QUESTIONS = get_questions_by_type('TEXT')
TABLE_QUESTIONS = get_questions_by_type('TABLE')
IMAGE_QUESTIONS = get_questions_by_type('IMAGE')

# Use in practice simulator
def generate_practice_question():
    # Mix different types
    question = random.choice(TEXT_QUESTIONS + TABLE_QUESTIONS + IMAGE_QUESTIONS)
    return question
```

### For Lecturefy (Lecture Notes)
```python
# Get IMAGE questions for visual examples
image_questions = get_questions_by_type('IMAGE')

# Use in lecture notes
for q in image_questions:
    add_example_section(
        question=q['question_text'],
        image=q['image_url'],
        answer=q['answer']
    )
```

### For ComicMaker (Comic Books)
```python
# Get questions for comic story
all_questions = get_all_questions_by_category()

# Create comic from questions
comic_panels = []
for q in all_questions:
    comic_panels.append({
        'text': q['question_text'],
        'image': q.get('image_url'),
        'answer': q['answer']
    })
```

---

## ✨ Benefits of Categorization

| Benefit | Description |
|---------|-------------|
| **Easy Filtering** | Extract questions by type with one API call |
| **Statistics** | Know exactly what you have (counts, distribution) |
| **Type-Specific Logic** | Handle each type appropriately |
| **Quality Control** | Identify missing images or malformed tables |
| **Content Planning** | See what topics need more questions |
| **Skill Development** | Build skills that work with specific types |

---

## 🔧 Extending the System

### Add Custom Categories

```python
def get_questions_with_setup_images():
    """Get questions that have setup diagrams"""
    all_questions = categorize_all_questions()
    with_images = []
    
    for qtype, questions in all_questions.items():
        if qtype == 'IMAGE':
            continue  # IMAGE type uses image as options
        with_images.extend([q for q in questions if q.image_url])
    
    return with_images

def get_questions_by_subtopic_and_type(subtopic: str, qtype: str):
    """Get questions filtered by both subtopic and type"""
    categorized = categorize_all_questions()
    filtered = [
        q for q in categorized.get(qtype, [])
        if q.subtopic.lower() == subtopic.lower()
    ]
    return filtered
```

### Add Type-Specific Validation

```python
def validate_question_category(question):
    """Validate that question matches its declared type"""
    if question.option_type == 'TABLE':
        assert question.table_headers, "TABLE question missing headers"
        assert question.table_rows, "TABLE question missing rows"
        assert len(question.table_rows) == 4, "TABLE question needs 4 rows"
    
    elif question.option_type == 'IMAGE':
        assert question.image_url, "IMAGE question missing image"
        assert not question.options.startswith('A)'), "IMAGE question should only have IMAGE:"
    
    elif question.option_type == 'TEXT':
        assert 'A)' in question.options, "TEXT question missing A)"
        assert 'B)' in question.options, "TEXT question missing B)"
        assert 'C)' in question.options, "TEXT question missing C)"
        assert 'D)' in question.options, "TEXT question missing D)"
```

---

## 📖 Next Steps

1. **Implement API endpoints** - Add code above to `quiz_backend.py`
2. **Test endpoints** - Visit http://localhost:8000/docs to test
3. **Use in Claude Code** - Reference examples in your scripts
4. **Build on this** - Extend with custom categories as needed

---

**Result: Claude Code can now easily extract, filter, and work with questions by type!** 🚀
