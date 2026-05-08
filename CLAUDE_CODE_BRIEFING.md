# Claude Code Briefing: Quiz Question Categorization System

This document explains everything Claude Code needs to know about the new question categorization system and how to integrate it into skills.

---

## Executive Summary

The quiz system now automatically categorizes all questions into three types:

- **TEXT** - Traditional A/B/C/D multiple choice (text options)
- **TABLE** - Structured data with headers and columns
- **IMAGE** - Diagram-based with image as options display

This enables skills to handle each type appropriately and extract questions easily via REST API.

---

## What Changed

### 1. Question Data Structure

Each question now includes three new fields:

```python
class Question(BaseModel):
    # Existing fields
    uid: str                           # Unique ID
    qno: str                          # Question number
    subtopic: str                     # Topic/subtopic
    difficulty: str                   # Easy/Medium/Hard
    question_text: str                # Main question
    options: str                      # Raw options text
    answer: str                       # Correct answer (A/B/C/D)
    image_url: Optional[str]          # Setup diagram URL
    
    # NEW CATEGORIZATION FIELDS:
    option_type: str                  # "TEXT" | "TABLE" | "IMAGE"
    table_headers: Optional[List[str]]    # Column headers for TABLE only
    table_rows: Optional[List[dict]]      # Row data for TABLE only
```

### 2. Automatic Type Detection

The backend automatically detects question type from the Google Sheet `Options` column:

- **IMAGE:** prefix → IMAGE type (uses Diagram column image)
- **TABLE:** prefix → TABLE type (parses headers and rows)
- Anything else → TEXT type (traditional options)

**Format Examples:**

TEXT (no prefix):
```
A) Option 1
B) Option 2
C) Option 3
D) Option 4
```

TABLE (with TABLE: prefix and pipe separators):
```
TABLE:
Property | Change
A) mass | no change
B) mass | decreases
C) weight | increases
D) weight | no change
```

IMAGE (just the prefix):
```
IMAGE:
```

### 3. Backend API Endpoints

Four new REST endpoints for extracting categorized questions:

**GET /api/questions/by-category**
Returns all questions grouped by type.
```json
{
  "TEXT": [{ question objects }],
  "TABLE": [{ question objects }],
  "IMAGE": [{ question objects }]
}
```

**GET /api/questions/type/{TYPE}**
Returns questions of specific type (TEXT, TABLE, or IMAGE).
```json
{
  "type": "TABLE",
  "count": 7,
  "questions": [{ question objects }]
}
```

**GET /api/statistics/categories**
Returns distribution statistics by type and difficulty.
```json
{
  "TEXT": {
    "count": 18,
    "percentage": 62.1,
    "by_difficulty": {"Easy": 8, "Medium": 7, "Hard": 3},
    "by_subtopic": {"Kinematics": 5, "Forces": 4, ...}
  },
  "TABLE": { ... },
  "IMAGE": { ... }
}
```

**GET /api/export/questions-by-category**
Returns complete export with metadata and export timestamp.

---

## How to Use in Skills

### Step 1: Create a Question Loader

Use this pattern in any skill:

```python
import requests

class QuestionLoader:
    def __init__(self, api_base_url="http://localhost:8000"):
        self.api_base = api_base_url
        self._cache = None
    
    def get_all_by_category(self):
        """Get all questions grouped by type"""
        if self._cache is None:
            response = requests.get(f"{self.api_base}/api/questions/by-category")
            self._cache = response.json()
        return self._cache
    
    def get_by_type(self, qtype):
        """Get questions of specific type"""
        response = requests.get(
            f"{self.api_base}/api/questions/type/{qtype.upper()}"
        )
        return response.json()['questions']
    
    def get_statistics(self):
        """Get category statistics"""
        response = requests.get(f"{self.api_base}/api/statistics/categories")
        return response.json()
```

### Step 2: Handle Each Question Type

When rendering or processing questions, check the `option_type`:

```python
def render_question(question):
    if question['option_type'] == 'TEXT':
        # Render traditional A/B/C/D options
        return render_text_options(question['options'])
    
    elif question['option_type'] == 'TABLE':
        # Render structured table with headers and rows
        return render_table_options(
            headers=question['table_headers'],
            rows=question['table_rows']
        )
    
    elif question['option_type'] == 'IMAGE':
        # Render diagram image with A/B/C/D buttons
        return render_image_options(
            image_url=question['image_url']
        )
```

---

## Integration Examples

### HabitGo (Practice Simulator)

```python
class PracticeGenerator:
    def __init__(self):
        self.loader = QuestionLoader()
    
    def generate_mixed_practice(self, subtopic, count=5):
        """Create practice with mixed question types"""
        all_questions = self.loader.get_all_by_category()
        
        # Filter by subtopic
        filtered = []
        for qtype, questions in all_questions.items():
            for q in questions:
                if q['subtopic'] == subtopic:
                    filtered.append(q)
        
        # Select variety of types
        import random
        random.shuffle(filtered)
        return filtered[:count]
    
    def show_practice_ui(self, questions):
        """Render each question appropriately"""
        for q in questions:
            print(f"\n{q['question_text']}")
            
            if q['option_type'] == 'TEXT':
                # Show radio buttons
                for line in q['options'].split('\n'):
                    print(f"  {line}")
            
            elif q['option_type'] == 'TABLE':
                # Show table
                headers = q['table_headers']
                print(f"\n  {' | '.join(headers)}")
                for row in q['table_rows']:
                    values = [row.get(h, '') for h in headers]
                    print(f"  {' | '.join(values)}")
            
            elif q['option_type'] == 'IMAGE':
                # Show image
                print(f"  [Diagram: {q['image_url']}]")
                print("  A) B) C) D)")
```

### Lecturefy (Lecture Notes)

```python
class NoteGenerator:
    def __init__(self):
        self.loader = QuestionLoader()
    
    def create_example_section(self, subtopic):
        """Create notes with examples from each type"""
        all_questions = self.loader.get_all_by_category()
        
        examples = {
            'text': [],
            'table': [],
            'image': []
        }
        
        # Collect by type
        for q in all_questions.get('TEXT', []):
            if q['subtopic'] == subtopic:
                examples['text'].append(q)
        
        for q in all_questions.get('TABLE', []):
            if q['subtopic'] == subtopic:
                examples['table'].append(q)
        
        for q in all_questions.get('IMAGE', []):
            if q['subtopic'] == subtopic:
                examples['image'].append(q)
        
        return examples
```

### ComicMaker (Educational Comics)

```python
class ComicAdapter:
    def __init__(self):
        self.loader = QuestionLoader()
    
    def convert_to_panels(self, subtopic):
        """Convert questions into comic panels"""
        all_questions = self.loader.get_all_by_category()
        panels = []
        
        for qtype in ['TEXT', 'TABLE', 'IMAGE']:
            for q in all_questions.get(qtype, []):
                if q['subtopic'] != subtopic:
                    continue
                
                panel = self._create_panel(q, qtype)
                panels.append(panel)
        
        return panels
    
    def _create_panel(self, question, qtype):
        """Create panel based on question type"""
        if qtype == 'TEXT':
            # Text dialogue panels
            return {
                'type': 'dialogue',
                'content': question['options']
            }
        elif qtype == 'TABLE':
            # Data visualization panels
            return {
                'type': 'data_table',
                'headers': question['table_headers'],
                'rows': question['table_rows']
            }
        elif qtype == 'IMAGE':
            # Visual/diagram panels
            return {
                'type': 'image',
                'image_url': question['image_url']
            }
```

---

## Key Implementation Details

### TABLE Type Structure

When `option_type == 'TABLE'`, the question includes:

```python
table_headers: ['Column1', 'Column2', 'Column3']
table_rows: [
    {'_letter': 'A', 'Column1': 'value1', 'Column2': 'value2', ...},
    {'_letter': 'B', 'Column1': 'value1', 'Column2': 'value2', ...},
    {'_letter': 'C', 'Column1': 'value1', 'Column2': 'value2', ...},
    {'_letter': 'D', 'Column1': 'value1', 'Column2': 'value2', ...}
]
```

Each row is a dict where:
- `_letter` is the option letter (A/B/C/D)
- Other keys match the `table_headers` field names

### IMAGE Type Structure

When `option_type == 'IMAGE'`:

```python
image_url: 'https://drive.google.com/file/d/.../view'
options: 'IMAGE:'  # Just the marker
table_headers: None
table_rows: None
```

The `image_url` is automatically fetched by the backend from the Google Drive Diagram column. The image itself IS the options display.

### TEXT Type Structure

When `option_type == 'TEXT'`:

```python
options: 'A) Option 1\nB) Option 2\nC) Option 3\nD) Option 4'
table_headers: None
table_rows: None
image_url: <setup diagram if exists>
```

Traditional format - split options by newline and render as radio buttons.

---

## Backend Utilities (Available Functions)

If your skill needs to call backend functions directly:

```python
# Import from quiz_backend
from quiz_backend import (
    categorize_all_questions,    # Get all questions by type
    get_questions_by_type,       # Get specific type
    get_category_statistics      # Get distribution stats
)

# Usage
all_qs = categorize_all_questions()  # Returns {'TEXT': [...], 'TABLE': [...], 'IMAGE': [...]}
text_qs = get_questions_by_type('TEXT')  # Returns list of TEXT questions
stats = get_category_statistics()  # Returns stats dict
```

---

## Common Patterns

### Filter by Multiple Criteria

```python
def get_questions(difficulty=None, subtopic=None, qtype=None):
    loader = QuestionLoader()
    all_questions = loader.get_all_by_category()
    
    results = []
    for q_type, questions in all_questions.items():
        for q in questions:
            if difficulty and q['difficulty'].lower() != difficulty.lower():
                continue
            if subtopic and q['subtopic'] != subtopic:
                continue
            if qtype and q['option_type'] != qtype.upper():
                continue
            results.append(q)
    
    return results

# Usage
easy_table_questions = get_questions(
    difficulty='Easy',
    qtype='TABLE'
)
```

### Create Balanced Quizzes

```python
def create_balanced_quiz(count=10):
    loader = QuestionLoader()
    stats = loader.get_statistics()
    
    # Calculate distribution
    quiz = []
    for qtype, data in stats.items():
        needed = int(count * (data['percentage'] / 100))
        questions = loader.get_by_type(qtype)
        
        import random
        selected = random.sample(questions, min(needed, len(questions)))
        quiz.extend(selected)
    
    import random
    random.shuffle(quiz)
    return quiz
```

### Group Questions Hierarchically

```python
def group_by_subtopic():
    loader = QuestionLoader()
    all_questions = loader.get_all_by_category()
    
    groups = {}
    for qtype, questions in all_questions.items():
        for q in questions:
            subtopic = q['subtopic']
            if subtopic not in groups:
                groups[subtopic] = {'TEXT': [], 'TABLE': [], 'IMAGE': []}
            groups[subtopic][qtype].append(q)
    
    return groups

# Usage
all_by_subtopic = group_by_subtopic()
kinematics = all_by_subtopic['Kinematics']  # Has TEXT, TABLE, IMAGE lists
```

---

## Error Handling

```python
import requests

def safe_load_questions(qtype):
    try:
        response = requests.get(
            f"http://localhost:8000/api/questions/type/{qtype}",
            timeout=5
        )
        response.raise_for_status()
        return response.json()['questions']
    
    except requests.exceptions.ConnectionError:
        print("ERROR: Backend not running. Start with: python quiz_backend.py")
        return []
    except requests.exceptions.HTTPError:
        print(f"ERROR: Invalid request or wrong type")
        return []
    except Exception as e:
        print(f"ERROR: {e}")
        return []
```

---

## Performance Tips

1. **Cache on startup** - Load questions once when skill initializes
2. **Reuse API responses** - Don't call the same endpoint repeatedly
3. **Filter early** - Get specific questions before rendering
4. **Type-specific logic** - Handle each type differently
5. **Handle missing data** - Check for None values in optional fields

---

## Summary for Skills

| Skill | Use Case | Key Pattern |
|-------|----------|------------|
| **HabitGo** | Mixed practice sets | Load by type, mix for variety |
| **Lecturefy** | Example galleries | Extract by subtopic, show all types |
| **ComicMaker** | Visual stories | Convert to panels by type |
| **Any Skill** | Question selection | Use statistics for balancing |

---

## Verification Checklist

Before deploying a skill:

- [ ] Backend running: `python quiz_backend.py`
- [ ] API responding: `curl http://localhost:8000/api/questions/by-category`
- [ ] Questions load correctly
- [ ] TEXT questions render with options
- [ ] TABLE questions show headers and rows
- [ ] IMAGE questions display diagrams with buttons
- [ ] No errors in loading or filtering
- [ ] Response times under 50ms

---

## Starting Your Skill

```python
# At skill initialization
from question_loader import QuestionLoader

class MySkill:
    def __init__(self):
        self.loader = QuestionLoader()
        self.questions = self.loader.get_all_by_category()
        print(f"✅ Loaded {sum(len(q) for q in self.questions.values())} questions")
    
    def use_questions(self, subtopic):
        # Get specific subset
        my_questions = []
        for qtype, questions in self.questions.items():
            for q in questions:
                if q['subtopic'] == subtopic:
                    my_questions.append(q)
        
        return my_questions
```

---

## Files and Resources

All documentation available in `C:\School\quizMaker\`:

- `CLAUDE_CODE_INTEGRATION.md` - Detailed integration examples
- `TESTING_CATEGORIZATION.md` - Testing guide and validation
- `QUESTION_CATEGORIZATION.md` - Complete categorization reference
- `CATEGORIZATION_SUMMARY.md` - Executive summary
- `auto_categorize_sheet.py` - Script that updates sheet automatically

---

**Ready to build skills with categorized questions!** 🚀

Use the QuestionLoader pattern shown above and refer to skill-specific examples in CLAUDE_CODE_INTEGRATION.md.
