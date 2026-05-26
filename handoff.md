# HabitGo / QuizMaker — Session Handoff

_Last updated: 2026-05-24_

## Goal

Two repos make up the HabitGo app:

- **Backend** — `C:\School\quizMaker` (FastAPI, `quiz_backend.py`), repo `quiz-maker-backend`
- **Frontend** — `C:\School\quiz-maker-frontend` (React 19 + Vite 8, plain `.jsx`), repo `quiz-maker-frontend`

This session worked the **"Build today's quiz" form** (`QuizMaker.jsx`, shared by the Daily quiz and Practice pages). Two threads:

1. **Quiz-builder filters** — make the physics level a dropdown, give Daily an "All topics" option, list the full Combined syllabus.
2. **Difficulty-availability feature** (from `HabitGo Difficulty Brief.pdf`) — difficulty cards (Easy/Medium/Hard) must react to the topic + question-count selection so a user can't build a quiz that errors with "Not enough questions".

## Current state of the code

All changes below are **committed-and-pushed OR working-tree only** — see "Next step".

### Backend — `quiz_backend.py`
- `/api/subtopics` now returns the **full syllabus topic list in order** (not just topics that have questions). `COMBINED_TOPIC_ORDER` holds the 16 Combined-Physics topics; `PURE_TOPIC_ORDER` the Pure list. Topic 1 spelled "Physical Quantities, Units and Measurements".
- **New endpoint `/api/availability?level=pure|combined`** — returns `{topic: {easy: N, medium: N, hard: N}}`, the question count per topic per difficulty. Drives the frontend greying. _Pushed status: working tree — not yet pushed._
- Also modified in the working tree: `.gitignore`, `.vscode/settings.json`. Untracked: a stray `package-lock.json` (looks accidental in a Python repo — decide whether to keep).

### Frontend — `QuizMaker.jsx`
- Physics level is a **dropdown** (`🧪 Pure Physics` / `⚛️ Combined Physics`, no placeholder, defaults to Pure). It's the first filter, inline with Topics/Difficulty/Count — no more gating page.
- **Daily quiz now has the "All topics" option**, same as Practice (the `isPractice` gates were removed, including the "pick at least 1 topic" validation).
- **Difficulty-availability feature** is built:
  - Module helpers `diffKey`, `countAt`, `difficultyAvailable`, `nearestValidDifficulty`.
  - `difficultyAvailable(dk, topics, availability, count)` mirrors the backend allocation: ≤3 topics, each needs `ceil(count / nTopics)` questions at that difficulty; "All topics" needs the level-wide pool to cover `count`.
  - Fetches `/api/availability` whenever the level changes.
  - Unavailable difficulty cards grey out (35% opacity, non-clickable); caption shows "Some difficulties unavailable…" / "No questions available for this topic."
  - A stale difficulty auto-snaps to the nearest valid one (easier side on a tie) with a scale-pop animation; re-runs on topic OR question-count change.
  - Submit button blocked + a hard guard in `handleCreateQuiz` so no empty/erroring quiz can be built.

Backend `py_compile` and frontend `esbuild` both pass clean.

## Files currently being edited

- `C:\School\quizMaker\quiz_backend.py`
- `C:\School\quiz-maker-frontend\src\components\QuizMaker.jsx`

## What was tried and failed / went wrong

- **Edit tool truncated large files.** Mid-session, the editor silently truncated both `quiz_backend.py` (cut at ~line 4530) and `QuizMaker.jsx` (cut at ~line 945). Caught via `py_compile` / `esbuild`. **Recovery that worked:** restore the file from `git show HEAD:<path>`, then re-apply every change with Python scripts run in the shell (the shell writes the real disk). All subsequent edits this session used shell Python, not the Edit tool. No work was lost — both files were restored from the last pushed HEAD.
- **First version of the feature was wrong.** It greyed a difficulty only when a topic had **zero** questions. The real backend errors when a topic has fewer than `ceil(count/nTopics)`. Fixed by making `/api/availability` return counts and the frontend check count-aware.
- **`git commit` is currently blocked** by a stale `C:\School\quizMaker\.git\index.lock`. Not yet cleared.
- The sandbox **cannot run `npm run build`** (Vite 8's rolldown native binary is missing) — frontend verification is done with `esbuild` syntax checks instead.
- `request_cowork_directory` was cancelled when trying to locate a separate `HabitGo.html` — turned out the app *is* the React frontend, no separate file.

## Next step

1. **Unblock the push.** Delete `C:\School\quizMaker\.git\index.lock` (no Git tool must be open), then commit + push the backend, then commit + push the frontend. Decide whether the stray `package-lock.json` should be committed.
2. **Restart the FastAPI backend** so `/api/availability` is served. Until then the frontend degrades gracefully (all difficulties stay enabled — no crash).
3. **`npm install`** in `C:\School\quiz-maker-frontend` — `framer-motion` is in `package.json` but not installed, so the dev server currently fails to resolve it in `main.jsx`.
4. **Smoke-test the feature** against the brief's matrix: pick Turning Effect of Forces + Radioactivity at count 10 → Hard should grey out; if Hard was selected it should auto-snap to Medium.

## Paused / not started this session

- **HabitGo UI level-up** (Request A) — install + use the UI/UX Pro Max skill and `framer-motion` to visually level up the app. Working clone of the skill is at `/tmp/uiux` in the sandbox; `framer-motion` v12 + `Motion.jsx` primitives + `MotionConfig` are already wired in code. Blocked on `npm install` and on scoping decisions.
- **YUANCHING2018 OCR re-check** (Request B) — re-check the extracted exam paper for two defect types: setup diagrams misplaced into the answer-options column (e.g. Q020), and watermark/junk text inside question text (e.g. Q018, `www.KiasuExamPaper.com`). The extracted Excel catalogue was never located — needs the file path.
- **StarQuest Phase 2B** (backlog task #130) — Bonus Round + weekly gems + `rank_history`.
