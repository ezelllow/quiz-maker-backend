# Complete React Frontend Setup Guide

## **OVERVIEW**

You now have 3 files to use in your React project:
1. `App.jsx` - Main app component
2. `App.css` - App styling
3. `QuizMaker.jsx` - Quiz maker component
4. `QuizMaker.css` - Quiz styling

---

## **STEP 1: Create React Project**

Open PowerShell/Command Prompt and run:

```bash
cd C:\School
npm create vite@latest quiz-maker-frontend -- --template react
cd quiz-maker-frontend
npm install
```

---

## **STEP 2: Add the Files**

### **Option A: Copy-Paste Method (Easiest)**

1. **Replace `src/App.jsx`:**
   - Open the `App.jsx` file provided in `C:\School\quizMaker\`
   - Copy all the code
   - In your React project, go to `src/App.jsx`
   - Replace everything with the copied code
   - Save it

2. **Replace `src/App.css`:**
   - Copy from `C:\School\quizMaker\App.css`
   - Paste into `src/App.css`
   - Save it

3. **Create `src/components/QuizMaker.jsx`:**
   - In your React project, create a `src/components` folder (if it doesn't exist)
   - Create a new file: `QuizMaker.jsx`
   - Copy the code from `C:\School\quizMaker\QuizMaker.jsx`
   - Paste it in
   - Save it

4. **Create `src/components/QuizMaker.css`:**
   - In the `src/components` folder
   - Create a new file: `QuizMaker.css`
   - Copy the code from `C:\School\quizMaker\QuizMaker.css`
   - Paste it in
   - Save it

### **Option B: Terminal Command Method**

From `quiz-maker-frontend` folder:

```bash
# Create components folder
mkdir src\components

# Copy files (Windows)
copy ..\quizMaker\App.jsx src\
copy ..\quizMaker\App.css src\
copy ..\quizMaker\QuizMaker.jsx src\components\
copy ..\quizMaker\QuizMaker.css src\components\
```

---

## **STEP 3: Your Folder Structure**

Should look like:

```
quiz-maker-frontend/
├── src/
│   ├── App.jsx          ✅ Replaced
│   ├── App.css          ✅ Replaced
│   ├── main.jsx         (Keep default)
│   ├── index.css        (Keep default)
│   └── components/
│       ├── QuizMaker.jsx    ✅ New
│       └── QuizMaker.css    ✅ New
├── public/
├── index.html
├── vite.config.js
├── package.json
└── ...
```

---

## **STEP 4: Start Everything**

You need **TWO terminals running**:

### **Terminal 1: Backend (Keep Running)**

```bash
cd C:\School\quizMaker
python quiz_backend.py
```

Expected output:
```
✅ Loaded 29 questions from sheet
📊 Available subtopics: [...]
💡 API will be available at http://localhost:8000
INFO:     Application startup complete.
```

### **Terminal 2: Frontend (Keep Running)**

```bash
cd C:\School\quiz-maker-frontend
npm run dev
```

Expected output:
```
VITE v5.x.x  ready in XXX ms

➜  Local:   http://localhost:5173/
```

---

## **STEP 5: Open in Browser**

Go to: **http://localhost:5173**

You should see:
- 📚 Quiz Maker header
- Filter form (Subtopic, Difficulty, Question Count)
- "🚀 Create Quiz" button

---

## **TESTING THE APP**

1. **Select filters:**
   - Subtopic: "Gravitational field"
   - Difficulty: "Easy"
   - Count: 3

2. **Click "Create Quiz"**

3. **Answer questions and check results**

---

## **FEATURES**

✅ **Filter Selection**
- Choose subtopic (optional)
- Choose difficulty (optional)
- Specify number of questions

✅ **Quiz Taking**
- Answer multiple choice questions
- View question images
- Navigate between questions
- See subtopic and difficulty

✅ **Results**
- Score percentage
- Review all answers
- See which ones you got right/wrong
- Take another quiz

---

## **TROUBLESHOOTING**

### **"Cannot fetch from http://localhost:8000"**
- Make sure backend is running: `python quiz_backend.py`
- Check if it says "Application startup complete"

### **Port 5173 already in use**
```bash
npm run dev -- --port 3000
```

### **Missing dependencies**
```bash
cd quiz-maker-frontend
npm install
```

### **Import errors**
- Make sure `QuizMaker.jsx` is in `src/components/`
- Make sure `QuizMaker.css` is in `src/components/`

---

## **NEXT STEPS**

- ✅ Build and deploy!
- ✅ Customize styling (colors, fonts, etc.)
- ✅ Add more features (export results, timer, etc.)

---

## **Build for Production**

When ready to deploy:

```bash
npm run build
```

Creates a `dist/` folder with your production-ready app!

---

**Happy Quiz Making! 🎉**
