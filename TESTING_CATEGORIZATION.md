# Testing the Categorization System

This guide shows how to test all the new categorization endpoints.

---

## 🚀 Quick Test

### 1. Start Backend

```bash
cd C:\School\quizMaker
python quiz_backend.py
```

You should see:
```
✅ Loaded 29 questions from sheet
📊 Question Categories:
  TEXT: 18 questions (62.1%)
  TABLE: 7 questions (24.1%)
  IMAGE: 4 questions (13.8%)
```

### 2. Test Endpoints

**Test in browser or curl:**

```bash
# Get all questions by category
curl http://localhost:8000/api/questions/by-category | python -m json.tool

# Get TEXT questions only
curl http://localhost:8000/api/questions/type/TEXT | python -m json.tool

# Get TABLE questions only
curl http://localhost:8000/api/questions/type/TABLE | python -m json.tool

# Get IMAGE questions only
curl http://localhost:8000/api/questions/type/IMAGE | python -m json.tool

# Get statistics
curl http://localhost:8000/api/statistics/categories | python -m json.tool

# Export all
curl http://localhost:8000/api/export/questions-by-category | python -m json.tool
```

### 3. View API Docs

Visit: http://localhost:8000/docs

You'll see all new endpoints listed:
- `/api/questions/by-category`
- `/api/questions/type/{qtype}`
- `/api/statistics/categories`
- `/api/export/questions-by-category`

---

## 📋 Detailed Tests

### Test 1: Get All Questions by Category

**Command:**
```bash
curl http://localhost:8000/api/questions/by-category | python -m json.tool
```

**Expected Response Structure:**
```json
{
  "TEXT": [
    {
      "uid": "...",
      "option_type": "TEXT",
      "table_headers": null,
      "table_rows": null,
      ...
    }
  ],
  "TABLE": [
    {
      "uid": "...",
      "option_type": "TABLE",
      "table_headers": ["Header1", "Header2"],
      "table_rows": [...]
      ...
    }
  ],
  "IMAGE": [
    {
      "uid": "...",
      "option_type": "IMAGE",
      "image_url": "https://...",
      ...
    }
  ]
}
```

**What to check:**
- ✅ All three types present
- ✅ Correct type labels
- ✅ TABLE has table_headers and table_rows
- ✅ IMAGE has image_url
- ✅ No mixing of fields between types

---

### Test 2: Get Specific Type

**Command:**
```bash
curl http://localhost:8000/api/questions/type/TABLE
```

**Expected Response:**
```json
{
  "type": "TABLE",
  "count": 7,
  "questions": [
    {
      "uid": "PHY-005",
      "qno": "Q5",
      "option_type": "TABLE",
      "table_headers": ["Property", "Change"],
      "table_rows": [
        {"_letter": "A", "Property": "mass", "Change": "no change"},
        ...
      ]
    }
  ]
}
```

**What to check:**
- ✅ Count matches expected
- ✅ All questions have correct type
- ✅ Table structure is valid
- ✅ Each row has "_letter" field

---

### Test 3: Get Statistics

**Command:**
```bash
curl http://localhost:8000/api/statistics/categories | python -m json.tool
```

**Expected Response:**
```json
{
  "TEXT": {
    "count": 18,
    "percentage": 62.1,
    "by_difficulty": {
      "Easy": 8,
      "Hard": 3,
      "Medium": 7
    },
    "by_subtopic": {
      "Energy": 3,
      "Forces": 4,
      "Kinematics": 5,
      ...
    },
    "has_images": true
  },
  "TABLE": {
    "count": 7,
    "percentage": 24.1,
    ...
  },
  "IMAGE": {
    "count": 4,
    "percentage": 13.8,
    ...
  }
}
```

**What to check:**
- ✅ Percentages add up to ~100%
- ✅ Counts match actual questions
- ✅ Difficulty levels correct
- ✅ Subtopics present

---

### Test 4: Filter by Type (Python)

**Script:**
```python
import requests

# Get TABLE questions only
response = requests.get('http://localhost:8000/api/questions/type/TABLE')
data = response.json()

print(f"Found {data['count']} TABLE questions")

# Extract specific information
for q in data['questions']:
    print(f"\nUID: {q['uid']}")
    print(f"Subtopic: {q['subtopic']}")
    print(f"Headers: {q['table_headers']}")
    print(f"Rows: {len(q['table_rows'])}")
```

**Expected Output:**
```
Found 7 TABLE questions

UID: PHY-005
Subtopic: Forces
Headers: ['Property', 'Change']
Rows: 4

UID: PHY-012
Subtopic: Energy
Headers: ['Object', 'Mass', 'Velocity']
Rows: 4
...
```

---

### Test 5: Export All Data

**Command:**
```bash
curl http://localhost:8000/api/export/questions-by-category > exported_questions.json
```

**Then inspect:**
```python
import json

with open('exported_questions.json') as f:
    data = json.load(f)

print("Exported at:", data['exported_at'])
print("Statistics:", data['statistics'])
print("Total questions:", sum(len(q) for q in data['questions'].values()))
```

---

### Test 6: Validate Question Structure

**Script:**
```python
import requests

def validate_question(q):
    """Validate that question structure is correct"""
    errors = []
    
    # Check required fields
    required = ['uid', 'qno', 'subtopic', 'difficulty', 'question_text', 'answer', 'option_type']
    for field in required:
        if field not in q or not q[field]:
            errors.append(f"Missing or empty: {field}")
    
    # Check type-specific fields
    qtype = q.get('option_type')
    
    if qtype == 'TEXT':
        if q.get('table_headers') is not None:
            errors.append("TEXT question has table_headers")
        if q.get('table_rows') is not None:
            errors.append("TEXT question has table_rows")
    
    elif qtype == 'TABLE':
        if not q.get('table_headers'):
            errors.append("TABLE question missing headers")
        if not q.get('table_rows'):
            errors.append("TABLE question missing rows")
        if len(q.get('table_rows', [])) != 4:
            errors.append(f"TABLE question has {len(q['table_rows'])} rows, need 4")
    
    elif qtype == 'IMAGE':
        if not q.get('image_url'):
            errors.append("IMAGE question missing image_url")
        if q.get('table_headers') is not None:
            errors.append("IMAGE question has table_headers")
    
    return errors

# Test all questions
response = requests.get('http://localhost:8000/api/questions/by-category')
all_questions = response.json()

errors_found = {}
for qtype, questions in all_questions.items():
    for q in questions:
        errors = validate_question(q)
        if errors:
            uid = q['uid']
            errors_found[uid] = errors

if errors_found:
    print("❌ Validation errors found:")
    for uid, errors in errors_found.items():
        print(f"\n{uid}:")
        for error in errors:
            print(f"  - {error}")
else:
    print("✅ All questions validated successfully!")
```

---

### Test 7: Performance Test

**Script:**
```python
import requests
import time

# Measure response time
endpoints = [
    '/api/questions/by-category',
    '/api/questions/type/TEXT',
    '/api/questions/type/TABLE',
    '/api/questions/type/IMAGE',
    '/api/statistics/categories',
]

print("Performance Test:")
print("-" * 50)

for endpoint in endpoints:
    start = time.time()
    response = requests.get(f'http://localhost:8000/api{endpoint}')
    elapsed = (time.time() - start) * 1000  # Convert to ms
    
    status = "✅" if response.status_code == 200 else "❌"
    print(f"{status} {endpoint}")
    print(f"   Response time: {elapsed:.2f}ms")
    print(f"   Status: {response.status_code}")
```

**Expected Performance:**
```
✅ /api/questions/by-category
   Response time: 15.32ms
   Status: 200

✅ /api/questions/type/TEXT
   Response time: 12.45ms
   Status: 200

✅ /api/questions/type/TABLE
   Response time: 10.88ms
   Status: 200

✅ /api/questions/type/IMAGE
   Response time: 9.76ms
   Status: 200

✅ /api/statistics/categories
   Response time: 14.21ms
   Status: 200
```

All should be **< 50ms** (very fast!)

---

### Test 8: Claude Code Integration

**Test that Claude Code can use the API:**

```python
# Claude Code Script
import requests

# Test 1: Load questions
response = requests.get('http://localhost:8000/api/questions/by-category')
questions = response.json()

# Test 2: Filter by type
text_questions = questions.get('TEXT', [])
table_questions = questions.get('TABLE', [])
image_questions = questions.get('IMAGE', [])

# Test 3: Use in logic
print(f"TEXT questions: {len(text_questions)}")
print(f"TABLE questions: {len(table_questions)}")
print(f"IMAGE questions: {len(image_questions)}")

# Test 4: Create quiz
selected = []
selected.extend(text_questions[:2])  # 2 TEXT
selected.extend(table_questions[:1])  # 1 TABLE
selected.extend(image_questions[:2])  # 2 IMAGE

print(f"\nCreated quiz with {len(selected)} questions")
for q in selected:
    print(f"  - {q['subtopic']}: {q['option_type']}")
```

**Expected Output:**
```
TEXT questions: 18
TABLE questions: 7
IMAGE questions: 4

Created quiz with 5 questions
  - Kinematics: TEXT
  - Forces: TEXT
  - Forces: TABLE
  - Optics: IMAGE
  - Waves: IMAGE
```

---

## ✅ Comprehensive Checklist

Run through this checklist to verify everything works:

### Backend
- [ ] Backend starts without errors
- [ ] Logs show category statistics on startup
- [ ] No errors in startup logs
- [ ] All imports successful

### Endpoints
- [ ] GET /api/questions/by-category returns all questions organized by type
- [ ] GET /api/questions/type/TEXT returns only TEXT questions
- [ ] GET /api/questions/type/TABLE returns only TABLE questions
- [ ] GET /api/questions/type/IMAGE returns only IMAGE questions
- [ ] GET /api/statistics/categories returns correct statistics
- [ ] GET /api/export/questions-by-category returns complete export

### Data Validation
- [ ] All questions have option_type field
- [ ] TEXT questions have no table_headers/table_rows
- [ ] TABLE questions have valid table_headers and table_rows
- [ ] IMAGE questions have image_url
- [ ] All answers are single letters (A/B/C/D)
- [ ] No questions are missing required fields

### Counts
- [ ] Total question count matches sheet
- [ ] TEXT + TABLE + IMAGE count = total
- [ ] Percentages add up to ~100%
- [ ] Category counts by difficulty add up correctly

### Performance
- [ ] All endpoints respond in < 50ms
- [ ] No timeout errors
- [ ] Memory usage is stable (not growing)

### Claude Code
- [ ] Can successfully load questions via API
- [ ] Can filter by type
- [ ] Can access table_headers and table_rows for TABLE type
- [ ] Can access image_url for IMAGE type
- [ ] Can build quizzes from categorized questions

---

## 🐛 Troubleshooting

### "Connection refused"
**Problem:** Backend not running
**Solution:** Start backend: `python quiz_backend.py`

### "Invalid type: X"
**Problem:** Using wrong type name
**Solution:** Use "TEXT", "TABLE", or "IMAGE" (uppercase)

### "Empty response"
**Problem:** Questions not loaded
**Solution:** Check that Google Sheet and Drive are accessible

### "Table missing rows"
**Problem:** TABLE question not properly formatted
**Solution:** Check OPTIONS column has `TABLE:` prefix and proper format

### "Image URL is null"
**Problem:** IMAGE question missing diagram
**Solution:** Check DIAGRAM column has file ID or image URL

---

## 📊 Expected Statistics

Based on the example questions, you should see something like:

```
TEXT: ~18 questions (60-65%)
TABLE: ~7 questions (20-25%)
IMAGE: ~4 questions (10-15%)
```

These percentages should match your Google Sheet distribution.

---

## 🎉 Success Indicators

You'll know everything is working when:

✅ Backend shows categories on startup
✅ All API endpoints respond in < 50ms
✅ Statistics match your question distribution
✅ Claude Code can load and use questions
✅ Question structures are valid
✅ Counts add up correctly

**If all checks pass, you're ready to use categorized questions in your Claude Code skills!** 🚀

---

## 📚 Next Steps

1. **Run the checklist** - Verify everything works
2. **Test in Claude Code** - Use QuestionLoader class
3. **Build skills** - Create practices, lectures, comics
4. **Monitor stats** - Check category distribution
5. **Optimize** - Adjust question types as needed

Happy building! 🎓
