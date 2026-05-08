# Quiz Maker Backend - Setup Guide

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Set Up Google Credentials

You need to provide your Google API credentials. There are two options:

**Option A: Environment Variable (Recommended)**
```bash
# Windows
set GOOGLE_APPLICATION_CREDENTIALS=path\to\your\credentials.json

# Mac/Linux
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/your/credentials.json

# Then run
python quiz_backend.py
```

**Option B: Place credentials.json in the same directory**
```bash
# Copy your credentials.json to the same folder as quiz_backend.py
python quiz_backend.py
```

### 3. Run the Server
```bash
python quiz_backend.py
```

You should see:
```
🚀 Loading questions on startup...
✅ Loaded X questions from sheet
📊 Available subtopics: [...]
📊 Available difficulties: [...]

🎯 Starting Quiz Maker Backend...
💡 API will be available at http://localhost:8000
📖 Docs at http://localhost:8000/docs
```

---

## API Endpoints

### 1. Get Available Subtopics
```
GET /api/subtopics
```

**Response:**
```json
[
  "Kinematics",
  "Force and Motion",
  "Energy and Work",
  ...
]
```

### 2. Get Available Difficulties
```
GET /api/difficulties
```

**Response:**
```json
["Easy", "Medium", "Hard"]
```

### 3. Create a Quiz
```
POST /api/quiz
```

**Request Body:**
```json
{
  "difficulty": "Medium",
  "subtopic": "Kinematics",
  "count": 5
}
```

**Response:**
```json
{
  "questions": [
    {
      "uid": "Q001",
      "qno": "1",
      "subtopic": "Kinematics",
      "difficulty": "Medium",
      "question_text": "A ball is thrown vertically upwards...",
      "options": "A) 10 m/s\nB) 20 m/s\nC) 30 m/s\nD) 40 m/s",
      "answer": "B",
      "image_url": "https://drive.google.com/uc?id=FILE_ID&export=download"
    },
    ...
  ],
  "count": 5,
  "filters": {
    "difficulty": "Medium",
    "subtopic": "Kinematics"
  }
}
```

### 4. Health Check
```
GET /health
```

---

## API Documentation

Once the server is running, visit:
- **Interactive Docs (Swagger UI):** http://localhost:8000/docs
- **Alternative Docs (ReDoc):** http://localhost:8000/redoc

You can test all endpoints directly in the browser!

---

## Using with Your React Frontend

### Example: Get Available Subtopics
```javascript
fetch('http://localhost:8000/api/subtopics')
  .then(res => res.json())
  .then(data => console.log(data))
```

### Example: Create a Quiz
```javascript
const request = {
  difficulty: "Medium",
  subtopic: "Kinematics",
  count: 5
};

fetch('http://localhost:8000/api/quiz', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(request)
})
  .then(res => res.json())
  .then(quiz => {
    console.log(`Got ${quiz.count} questions`);
    quiz.questions.forEach(q => {
      console.log(q.question_text);
      console.log(q.image_url);
    });
  })
```

---

## Filtering Options

### Optional Filters (both are optional)
- **difficulty**: Filter by difficulty level (case-insensitive)
  - Example: "Easy", "Medium", "Hard"
  - Get available values from `/api/difficulties`

- **subtopic**: Filter by subtopic (case-insensitive)
  - Example: "Kinematics", "Force and Motion"
  - Get available values from `/api/subtopics`

### Required Parameter
- **count**: Number of questions to return (minimum 1)

### Examples

**Get 5 random easy questions:**
```json
{
  "difficulty": "Easy",
  "count": 5
}
```

**Get 10 medium kinematics questions:**
```json
{
  "difficulty": "Medium",
  "subtopic": "Kinematics",
  "count": 10
}
```

**Get 3 random questions (no filters):**
```json
{
  "count": 3
}
```

---

## How It Works

1. **On Startup:** Backend loads all questions from your Google Sheet and caches them in memory
2. **On Request:** 
   - Filters questions by difficulty/subtopic
   - Randomly selects the requested number
   - Fetches image URLs from Google Drive
   - Returns formatted JSON
3. **Image Caching:** Image URLs are cached so repeated requests for the same question are fast

---

## Troubleshooting

### "Credentials not found!"
Make sure you've either:
1. Set `GOOGLE_APPLICATION_CREDENTIALS` environment variable, OR
2. Placed `credentials.json` in the quiz_backend.py directory

### "No questions found in sheet"
- Check that `SPREADSHEET_ID` in the script matches your Google Sheet ID
- Verify the sheet name (default is "Sheet1" - change if different)
- Make sure the sheet has the correct columns: UID, QNo, Subtopic, Difficulty, Question text, Options, Answer

### "Image URLs not loading"
- Verify `QUESTION_FOLDER_ID` is correct
- Check that image filenames match question UIDs exactly
- Make sure the Google Service Account has read access to the Drive folder

### "ModuleNotFoundError"
Run: `pip install -r requirements.txt`

---

## Next Steps

1. ✅ Backend is running on http://localhost:8000
2. 🎨 Build a React frontend to call these endpoints
3. 📱 Frontend shows filter dropdowns → calls `/api/subtopics` and `/api/difficulties`
4. 📝 Students select filters and number of questions → calls `/api/quiz`
5. 📖 Display returned questions with images and options

---

## Production Deployment

For production, you'll want to:
1. Host on a proper server (Heroku, PythonAnywhere, AWS Lambda, etc.)
2. Change `allow_origins` in CORS middleware to your frontend domain
3. Add authentication/authorization if needed
4. Use environment variables for sensitive data (IDs, credentials)
5. Add request logging and monitoring
