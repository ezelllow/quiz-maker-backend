# 🚀 Quick Start - 5 Minute Setup

## What You Need To Do

Your backend and frontend are **already updated**. You just need to update your Google Sheet.

### Step 1: Understand the Three Formats (2 min)

**TEXT** - Normal (leave as-is):
```
A) Option text
B) Option text
C) Option text
D) Option text
```

**TABLE** - Add `TABLE:` prefix:
```
TABLE:
Header | Header2
A) Val | Val
B) Val | Val
C) Val | Val
D) Val | Val
```

**IMAGE** - Just write `IMAGE:`:
```
IMAGE:
```

### Step 2: Update Your Google Sheet (2 min)

1. **Go to Paper1 sheet**
2. **Find questions with TABLE format** (like mass/weight one)
   - Change `A mass | no change` → `TABLE:\nProperty | Effect\nA) mass | no change`
3. **Find questions with IMAGE format** (like pendulum)
   - Change descriptions → `IMAGE:`
4. **Leave TEXT questions alone**

### Step 3: Test (1 min)

```bash
# Terminal 1
cd C:\School\quizMaker
python quiz_backend.py

# Terminal 2
cd C:\School\quiz-maker-frontend
npm run dev
```

Open http://localhost:5173 and create a quiz! ✨

---

## Format Templates

### Copy-Paste These

**TEXT (No change needed):**
```
A) First
B) Second
C) Third
D) Fourth
```

**TABLE:**
```
TABLE:
Column1 | Column2
A) Val1 | Val2
B) Val3 | Val4
C) Val5 | Val6
D) Val7 | Val8
```

**IMAGE:**
```
IMAGE:
```

---

## Key Rules

1. **TABLE**: Separate columns with `|`
2. **IMAGE**: Put image in Diagram column
3. **Answer**: Always just A, B, C, or D (no change!)
4. TEXT questions: Leave exactly as-is

---

## If Stuck

- TABLE not showing? Check for `TABLE:` prefix
- Image not showing? Check Diagram column has image
- Questions look weird? Refresh browser

---

## That's It! 🎉

Your system now supports:
- ✅ TEXT options (radio buttons)
- ✅ TABLE options (HTML table)
- ✅ IMAGE options (diagram + buttons)

No code changes needed. Just update your sheet and test!

---

**For detailed help:**
- See `SHEET_EXAMPLES.md` for copy-paste examples
- See `IMPLEMENTATION_CHECKLIST.md` for testing
- See `README_OPTION_TYPES.md` for full overview
