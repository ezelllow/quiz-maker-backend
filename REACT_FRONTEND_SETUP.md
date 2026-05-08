# React Quiz Maker Frontend - Complete Setup Guide

## **STEP 1: Create React Project with Vite**

Open a terminal (PowerShell or Command Prompt) and run:

```bash
cd C:\School
npm create vite@latest quiz-maker-frontend -- --template react
```

Press Enter to confirm. This creates a new folder `quiz-maker-frontend`.

---

## **STEP 2: Install Dependencies**

```bash
cd quiz-maker-frontend
npm install
```

Wait for installation to complete.

---

## **STEP 3: Start the Development Server**

```bash
npm run dev
```

You'll see:
```
VITE v5.x.x  ready in XXX ms

➜  Local:   http://localhost:5173/
➜  Press h + enter to show help
```

**Keep this terminal running!** Your React app is now live at http://localhost:5173

---

## **STEP 4: Project Structure**

Your `quiz-maker-frontend` folder should look like:
```
quiz-maker-frontend/
├── src/
│   ├── App.jsx          (Replace with the provided code)
│   ├── App.css
│   ├── main.jsx
│   ├── index.css
│   └── components/
│       └── QuizMaker.jsx (New file - provided)
├── public/
├── index.html
├── vite.config.js
├── package.json
└── ...
```

---

## **STEP 5: Update Files**

Replace the files with the code provided in the next section.

---

## **Backend Connection**

The React app will connect to your backend at:
```
http://localhost:8000
```

Make sure your backend is still running in another terminal!

```bash
python quiz_backend.py
```

---

## **Testing**

1. Backend running? ✅ `http://localhost:8000/docs`
2. React running? ✅ `http://localhost:5173`
3. Try the quiz maker! 🎯

---

## **Troubleshooting**

**"Cannot GET /api/quiz"**
- Make sure backend is running: `python quiz_backend.py`

**Port 5173 already in use**
- Change the port: `npm run dev -- --port 3000`

**CORS errors**
- Backend already has CORS enabled, should work automatically

---

## **Next: Building & Deployment**

When you're ready to deploy:

```bash
npm run build
```

This creates a `dist/` folder with your production-ready app.

---

Ready to add the code? Let me know! 👇
