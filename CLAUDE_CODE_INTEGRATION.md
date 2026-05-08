# Claude Code Integration Guide

This guide shows you how to use the categorized questions API with Claude Code skills like HabitGo, Lecturefy, and ComicMaker.

---

## 🚀 Quick Reference

### API Endpoints Available

```
GET /api/questions/by-category
  → Get all questions grouped by type

GET /api/questions/type/{TYPE}
  → Get questions of specific type (TEXT, TABLE, or IMAGE)

GET /api/statistics/categories
  → Get statistics on question distribution

GET /api/export/questions-by-category
  → Export all data for processing
```

---

## 🔗 Using in Claude Code Skills

### Pattern 1: Load Questions in a Skill

**In any skill that needs questions:**

```python
import requests

class QuestionLoader:
    def __init__(self, api_base_url="http://localhost:8000"):
        self.api_base = api_base_url
    
    def get_all_by_category(self):
        """Get all questions organized by type"""
        response = requests.get(f"{self.api_base}/api/questions/by-category")
        return response.json()
    
    def get_by_type(self, qtype):
        """Get questions of specific type"""
        qtype = qtype.upper()
        response = requests.get(f"{self.api_base}/api/questions/type/{qtype}")
        return response.json()['questions']
    
    def get_statistics(self):
        """Get category statistics"""
        response = requests.get(f"{self.api_base}/api/statistics/categories")
        return response.json()
    
    def export_all(self):
        """Export everything"""
        response = requests.get(f"{self.api_base}/api/export/questions-by-category")
        return response.json()

# Use it
loader = QuestionLoader()
all_questions = loader.get_all_by_category()
text_questions = loader.get_by_type('TEXT')
stats = loader.get_statistics()
```

---

## 📚 Skill-Specific Examples

### HabitGo (Practice Simulator)

**Goal:** Create practice sets with mixed question types

```python
from question_loader import QuestionLoader

class HabitGoPracticeGenerator:
    def __init__(self):
        self.loader = QuestionLoader()
    
    def generate_mixed_practice(self, difficulty, subtopic, count=5):
        """Generate practice with all three question types"""
        all_questions = self.loader.get_all_by_category()
        
        # Get questions matching criteria
        filtered = []
        for qtype, questions in all_questions.items():
            for q in questions:
                if (q['difficulty'].lower() == difficulty.lower() and
                    q['subtopic'] == subtopic):
                    filtered.append(q)
        
        # Mix types evenly
        text_qs = [q for q in filtered if q['option_type'] == 'TEXT']
        table_qs = [q for q in filtered if q['option_type'] == 'TABLE']
        image_qs = [q for q in filtered if q['option_type'] == 'IMAGE']
        
        # Select from each type
        practice_set = []
        import random
        
        if len(text_qs) > 0:
            practice_set.append(random.choice(text_qs))
        if len(table_qs) > 0:
            practice_set.append(random.choice(table_qs))
        if len(image_qs) > 0:
            practice_set.append(random.choice(image_qs))
        
        # Fill remaining with random questions
        while len(practice_set) < count:
            q = random.choice(filtered)
            if q not in practice_set:
                practice_set.append(q)
        
        return practice_set
    
    def get_type_specific_practice(self, qtype, subtopic):
        """Get practice set with single question type"""
        questions = self.loader.get_by_type(qtype)
        filtered = [q for q in questions if q['subtopic'] == subtopic]
        return filtered

# Use it
generator = HabitGoPracticeGenerator()

# Mixed practice
mixed = generator.generate_mixed_practice('Easy', 'Kinematics', count=5)

# Type-specific practice
table_practice = generator.get_type_specific_practice('TABLE', 'Forces')
```

---

### Lecturefy (Lecture Notes)

**Goal:** Create notes with examples from each question type

```python
from question_loader import QuestionLoader

class LecturefyNoteGenerator:
    def __init__(self):
        self.loader = QuestionLoader()
    
    def generate_notes_with_examples(self, subtopic):
        """Generate notes with examples from all question types"""
        all_questions = self.loader.get_all_by_category()
        
        notes = {
            'subtopic': subtopic,
            'text_examples': [],
            'table_examples': [],
            'visual_examples': []
        }
        
        # Extract examples by type
        for q in all_questions.get('TEXT', []):
            if q['subtopic'] == subtopic:
                notes['text_examples'].append(q)
        
        for q in all_questions.get('TABLE', []):
            if q['subtopic'] == subtopic:
                notes['table_examples'].append(q)
        
        for q in all_questions.get('IMAGE', []):
            if q['subtopic'] == subtopic:
                notes['visual_examples'].append(q)
        
        return notes
    
    def create_example_gallery(self, subtopic):
        """Create visual gallery of all example types"""
        notes = self.generate_notes_with_examples(subtopic)
        
        gallery = f"""
        # {subtopic} - Example Gallery
        
        ## Conceptual Examples (Text-based)
        {len(notes['text_examples'])} examples available
        
        ## Data Comparisons (Table-based)
        {len(notes['table_examples'])} examples available
        
        ## Visual Diagrams (Image-based)
        {len(notes['visual_examples'])} examples available
        """
        
        return gallery

# Use it
generator = LecturefyNoteGenerator()

# Generate notes with examples
notes = generator.generate_notes_with_examples('Kinematics')

# Create gallery
gallery = generator.create_example_gallery('Forces')
```

---

### ComicMaker (Educational Comics)

**Goal:** Create comic panels from questions

```python
from question_loader import QuestionLoader

class ComicMakerQuestionAdapter:
    def __init__(self):
        self.loader = QuestionLoader()
    
    def convert_to_comic_panels(self, subtopic):
        """Convert questions into comic panels"""
        all_questions = self.loader.get_all_by_category()
        
        panels = []
        
        # Create panels from each question type
        for qtype in ['TEXT', 'TABLE', 'IMAGE']:
            questions = all_questions.get(qtype, [])
            
            for q in questions:
                if q['subtopic'] != subtopic:
                    continue
                
                panel = {
                    'type': qtype,
                    'title': q['question_text'][:50] + "...",
                    'full_question': q['question_text'],
                    'answer': q['answer'],
                    'image': q.get('image_url'),
                    'options': self._format_options_for_comic(q)
                }
                panels.append(panel)
        
        return panels
    
    def _format_options_for_comic(self, question):
        """Format options appropriately for comic display"""
        qtype = question['option_type']
        
        if qtype == 'TEXT':
            # Show as dialogue
            lines = question['options'].split('\n')
            return [line.replace('A)', '').replace('B)', '').replace('C)', '').replace('D)', '') 
                   for line in lines if line.strip()]
        
        elif qtype == 'TABLE':
            # Show as data table
            return {
                'headers': question['table_headers'],
                'rows': question['table_rows']
            }
        
        elif qtype == 'IMAGE':
            # Show image with options as labels
            return {
                'image': question['image_url'],
                'options': ['A', 'B', 'C', 'D']
            }
    
    def create_story_arc(self, subtopic):
        """Create narrative structure using questions"""
        panels = self.convert_to_comic_panels(subtopic)
        
        # Group by difficulty for story progression
        easy = [p for p in panels if p.get('difficulty') == 'Easy']
        medium = [p for p in panels if p.get('difficulty') == 'Medium']
        hard = [p for p in panels if p.get('difficulty') == 'Hard']
        
        # Create story: Easy → Medium → Hard
        story = {
            'introduction': easy[:2] if easy else [],
            'conflict': medium[:2] if medium else [],
            'resolution': hard[:2] if hard else []
        }
        
        return story

# Use it
adapter = ComicMakerQuestionAdapter()

# Convert to panels
panels = adapter.convert_to_comic_panels('Optics')

# Create story
story = adapter.create_story_arc('Energy')
```

---

## 🎯 Common Patterns

### Pattern: Filter by Multiple Criteria

```python
def get_questions(difficulty=None, subtopic=None, qtype=None):
    """Get questions matching multiple criteria"""
    loader = QuestionLoader()
    all_questions = loader.get_all_by_category()
    
    results = []
    for q_type, questions in all_questions.items():
        for q in questions:
            # Apply filters
            if difficulty and q['difficulty'].lower() != difficulty.lower():
                continue
            if subtopic and q['subtopic'] != subtopic:
                continue
            if qtype and q['option_type'] != qtype:
                continue
            
            results.append(q)
    
    return results

# Use it
easy_text_kinematics = get_questions(
    difficulty='Easy',
    subtopic='Kinematics',
    qtype='TEXT'
)
```

### Pattern: Group Questions Hierarchically

```python
def group_questions(group_by='subtopic'):
    """Group questions by specified field"""
    loader = QuestionLoader()
    all_questions = loader.get_all_by_category()
    
    groups = {}
    for qtype, questions in all_questions.items():
        for q in questions:
            key = q[group_by]
            if key not in groups:
                groups[key] = {'TEXT': [], 'TABLE': [], 'IMAGE': []}
            groups[key][qtype].append(q)
    
    return groups

# Use it
by_subtopic = group_questions('subtopic')
by_difficulty = group_questions('difficulty')

# Access
kinematics_questions = by_subtopic['Kinematics']
easy_questions = by_difficulty['Easy']
```

### Pattern: Create Balanced Quizzes

```python
def create_balanced_quiz(count=10):
    """Create quiz with balanced question types"""
    loader = QuestionLoader()
    stats = loader.get_statistics()
    
    # Calculate how many of each type
    type_distribution = {}
    for qtype, data in stats.items():
        percentage = data['percentage'] / 100
        type_distribution[qtype] = int(count * percentage)
    
    # Get questions of each type
    quiz_questions = []
    for qtype, needed in type_distribution.items():
        questions = loader.get_by_type(qtype)
        import random
        selected = random.sample(questions, min(needed, len(questions)))
        quiz_questions.extend(selected)
    
    # Shuffle
    import random
    random.shuffle(quiz_questions)
    
    return quiz_questions

# Use it
balanced_quiz = create_balanced_quiz(count=20)
```

---

## 📊 Statistics Usage

```python
loader = QuestionLoader()
stats = loader.get_statistics()

# View breakdown by type
for qtype, data in stats.items():
    print(f"{qtype}:")
    print(f"  Count: {data['count']}")
    print(f"  Percentage: {data['percentage']}%")
    print(f"  By Difficulty: {data['by_difficulty']}")
    print(f"  By Subtopic: {data['by_subtopic']}")
```

**Output example:**
```
TEXT:
  Count: 18
  Percentage: 62.1%
  By Difficulty: {'Easy': 8, 'Medium': 7, 'Hard': 3}
  By Subtopic: {'Kinematics': 5, 'Forces': 4, ...}

TABLE:
  Count: 7
  Percentage: 24.1%
  By Difficulty: {'Easy': 2, 'Medium': 3, 'Hard': 2}
  By Subtopic: {'Forces': 3, 'Energy': 2, ...}

IMAGE:
  Count: 4
  Percentage: 13.8%
  By Difficulty: {'Hard': 4}
  By Subtopic: {'Optics': 3, 'Waves': 1}
```

---

## 🔧 Error Handling

```python
import requests

def safe_load_questions(qtype):
    """Load questions with error handling"""
    try:
        response = requests.get(
            f"http://localhost:8000/api/questions/type/{qtype}",
            timeout=5
        )
        response.raise_for_status()
        return response.json()['questions']
    
    except requests.exceptions.ConnectionError:
        print("❌ Backend not running. Start with: python quiz_backend.py")
        return []
    
    except requests.exceptions.HTTPError as e:
        print(f"❌ Error: {e}")
        return []
    
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return []

# Use it
questions = safe_load_questions('TEXT')
if questions:
    # Process questions
    pass
else:
    # Handle error
    pass
```

---

## 🚀 Getting Started

1. **Start the backend:**
   ```bash
   cd C:\School\quizMaker
   python quiz_backend.py
   ```

2. **Test the API:**
   ```bash
   curl http://localhost:8000/api/statistics/categories
   ```

3. **Use in skills:**
   - Copy the `QuestionLoader` class into your skill
   - Use the patterns above
   - Access categorized questions easily!

4. **Extend as needed:**
   - Add custom filters
   - Create specialized loaders
   - Build skill-specific adapters

---

## 💡 Tips

✅ **Cache responses** - Store results to avoid repeated API calls
✅ **Use pagination** - For large datasets, request in chunks
✅ **Type-specific logic** - Handle TEXT/TABLE/IMAGE differently
✅ **Error handling** - Always handle network errors gracefully
✅ **Preload on startup** - Load all questions once at skill initialization

---

## 📚 Reference

### Question Object Structure

```json
{
  "uid": "PHY-001",
  "qno": "Q1",
  "subtopic": "Kinematics",
  "difficulty": "Easy",
  "question_text": "...",
  "options": "...",
  "answer": "A",
  "image_url": "https://..." or null,
  "option_type": "TEXT" or "TABLE" or "IMAGE",
  "table_headers": [...] or null,
  "table_rows": [...] or null
}
```

### API Response Formats

**GET /api/questions/type/TEXT:**
```json
{
  "type": "TEXT",
  "count": 18,
  "questions": [...]
}
```

**GET /api/statistics/categories:**
```json
{
  "TEXT": {
    "count": 18,
    "percentage": 62.1,
    "by_difficulty": {...},
    "by_subtopic": {...}
  },
  ...
}
```

---

**Ready to integrate?** Copy the `QuestionLoader` class and start using categorized questions in your skills! 🚀
