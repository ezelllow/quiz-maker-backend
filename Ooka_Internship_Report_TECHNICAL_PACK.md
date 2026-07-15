# Ooka Internship Report — Technical Content Pack

**Prepared for:** Ezell Low Qing Wei (DCPE/FT/3B/99) · Full-Stack Developer Intern, Curious Lab
**Purpose:** Drafted technical content for the *Conceiving & Designing* and *Implementing & Operating* sections of the SP internship report, plus the *Hardest Problems* narrative. Written from the daily journal and the actual codebase (`quiz_backend.py`, `quiz-maker-frontend/`, handoff docs). Placeholders marked **`[FILL: …]`** are facts only you can supply — mostly numbers and short anecdotes. I list all of them at the end.

> Naming note: the app was **HabitGo → (QuizQuest) → Ooka**. This pack describes the final state (**Ooka**) and treats the rebrand as part of the design journey. Company = **Curious Lab**.

---

## 1. Project Overview (the one-paragraph framing)

Over my 22-week internship at Curious Lab I was the full-stack developer for **Ooka**, a gamified O-Level / Combined-Science study app that helps Singapore students build a consistent, effective daily study habit by practising on real exam-paper questions. The work had two halves that build towards the same goal. First, I built an **exam-paper extraction pipeline** that turns PDF/DOCX exam papers into a clean, structured question bank (questions, answers, diagrams, difficulty, marks, syllabus codes) held in Google Sheets and Google Drive. Second, I built the **Ooka app itself** — a React front end and a FastAPI + MySQL back end — that draws on that question bank to let students generate practice quizzes, complete a daily challenge, maintain streaks, earn XP and gems, climb a levelling system, customise a monkey avatar, and compete on a leaderboard, while teachers get a read-only class dashboard. In short: the pipeline manufactures the content, and the app turns that content into a daily habit-forming study loop.

---

## 2. Conceiving & Designing

### 2.1 Problem & scope
Curious Lab needed two things that did not exist yet: (a) a fast, repeatable way to convert the large backlog of real exam papers into a *digital, queryable* question bank, because manually retyping papers and re-drawing diagrams does not scale; and (b) a student-facing product that makes daily practice sticky rather than a chore. The deliverable was a working, deployed app backed by an automated content pipeline, covering Pure Physics and Combined Science (Physics) across the G1/G2/G3 bands, and later extended to P6 Mathematics.

**Outcome (by end of internship):** ~**130 exam papers** processed into the bank — roughly **91 Combined G3, 24 Pure Physics, 7 Combined G2, 3 Combined G1, and 5 P6 Math (PSLE)** — and Ooka deployed and in a **test phase with ~22 students**.

### 2.2 Key design decisions (and the trade-offs behind them)
These are the decisions that shaped the whole system — they are exactly the CDIO "Design" material the report wants.

**Questions live in Google Sheets, not in MySQL.** MySQL stores only *user* data (accounts, attempts, streaks, gems, XP). The *content* (questions, answers, diagram references) lives in Google Sheets, with diagram images in Google Drive. The trade-off: I gave up rich SQL querying and free-text search over questions, but in return non-technical content editors (teachers) can add and correct questions directly in a familiar spreadsheet — with comments, revision history and sharing — without ever touching a database. Filtering by subject/topic/difficulty happens in Python in memory after load, which is fast enough at this scale.

**An in-memory question cache instead of hitting Sheets per request.** Reading the Sheets API on every quiz request would be far too slow and would blow through API quotas. Instead the backend loads the whole bank once into a `QuestionCache` (a Python list held in memory) and reuses it for the process lifetime. The trade-off is that a Sheet edit isn't picked up until the backend restarts — an accepted constraint, since content is edited in batches.

**Single-process deployment.** Because the question cache, the Drive image cache and the Drive file-map all live in process memory, the service is deliberately run as **one** process/replica (`numReplicas = 1`). This keeps the design simple and consistent; the trade-off is that horizontal scaling would require moving those caches to a shared store (e.g. Redis) later.

**A monolithic FastAPI backend.** The entire API is one FastAPI file (`quiz_backend.py`, ~5,000+ lines) rather than a micro-service split. For a solo developer on a fast-moving product, one file meant no cross-service coordination and very fast iteration; the trade-off is that the file is large and would need modularising before a team scaled up on it.

### 2.3 System architecture
The system has four moving parts:

- **Front end** — React 19 + Vite 8, styled with Tailwind CSS and animated with Framer Motion. Single-page app; user state lives in `App.jsx` and is passed down by props (no Redux). Routes are lazy-loaded to keep the initial bundle small.
- **Back end** — FastAPI served by uvicorn; `mysql-connector-python` for the database; JWT auth (PyJWT) with bcrypt password hashing (passlib); Google APIs via a service account.
- **Database** — MySQL 8, seven tables, all cascading from `users`. The schema is created and migrated at startup by `init_database()`, where every `ALTER TABLE` is guarded by an `information_schema` column check so the migration is idempotent and safe to re-run.
- **External storage** — Google Sheets (question bank, one workbook with per-level tabs: `Pure Physics`, `combinedG3`, `combinedG2`, `combinedG1`, plus a separate P6 Math workbook) and Google Drive (one image folder per level, proxied to the browser through the backend).

> **Diagram to include (Fig.):** a simple architecture block diagram — `Browser (React SPA)` → `FastAPI backend` → { `MySQL (user data)`, `Google Sheets (questions)`, `Google Drive (diagrams)` }. I can generate this as an image for the report.

### 2.4 The data model (the seven tables)
`users` (identity, XP, gems, equipped cosmetics, daily goal, teacher flag); `quiz_attempts` (one row per quiz — score, %, time, a JSON blob of the exact questions and answers, and a self-reference `parent_attempt_id` for retakes); `streaks` (per-user current/longest streak and freeze state); `daily_challenges` (per user, per subject, per day — accumulates today's progress and XP); `user_rewards` (shop purchases, with a `UNIQUE(user_id, reward_id)` guard against double-redemption); and the legacy `user_subject_ranks` + `rank_history` from the pre-XP rank system (kept but orphaned).

---

## 3. Implementing & Operating

### 3.1 The extraction pipeline (content manufacturing)
This is the work of roughly weeks 1–6 (and revisited in weeks 17–21). The pipeline evolved in stages:

1. **OCR / text extraction.** I researched OCR and its limits, then used **pytesseract** to pull text out of exam-paper PDFs and push it into Google Sheets.
2. **Question splitting.** Turning a whole paper into individual questions was the first hard problem. I first tried local LLMs through **GPT4All** (e.g. Llama 3 8B Instruct), but the smaller models split questions unreliably. I moved to using **Claude** to do the splitting and classification, which was far more consistent.
3. **Structuring.** Each question became a row with the question text, answer, the associated **diagram image**, and classification columns — `Difficulty`, `Marks`, `Subtopic`, the question *setup/context*, and the **SIO (Standard Instructional Objective) code**, which I matched automatically to each question by how well it fit each objective.
4. **Paper 1 vs Paper 2.** I built the multiple-choice (Paper 1) and structured (Paper 2) flows separately because their layouts differ. Paper 1 fed the app's quiz engine directly.
5. **Syllabus migration.** Mid-internship the content had to move to the new MOE syllabus, so I restructured the sheets and the pipeline to distinguish **Combined G1 / G2 / G3 and Pure Physics** as separate levels (the `combinedG1` tab even uses different column headers, which the loader handles). I also added an **explanation column** where Claude writes an explanation when the marking scheme doesn't include one.
6. **New subject — P6 Math.** I stood up a separate sheet, image drive and extraction flow for Primary 6 Mathematics, adding special handling for maths symbols and fractions, and used an LLM to convert non-MCQ questions into MCQ form so they fit the quiz engine.

### 3.2 Question data flow into the app
`QuestionCache._load_questions_unlocked()` batch-fetches every configured Sheet tab in one API call, with a per-tab fallback so one stale/renamed tab can't take down the whole quiz endpoint. Rows are merged (re-mapped by header name so the P6 workbook merges cleanly), and each becomes a `Question` object. Diagram references written in Sheet cells as `IMAGE:filename.png` are resolved to real Drive file IDs via a `file_map` built at startup.

### 3.3 Quiz generation
The student picks a level (Pure or Combined), up to three topics from an official-syllabus-locked list, a difficulty, and a count. Before submitting, the front end calls `/api/availability` and **greys out** difficulty tiles that can't cover the requested count, and **auto-snaps** to the nearest valid difficulty (with a visible animation, never a silent change). The backend filters the cached questions, splits the count across topics, random-samples from each pool, resolves image URLs, and returns the quiz.

### 3.4 The gamified study loop
- **Daily challenge:** not a fixed 10-question set but a *running counter* — keep answering until you hit your daily goal (default 10 correct). This lets students do several short bursts across the day and still qualify.
- **Streaks + freeze:** hitting the daily goal extends the streak; each user gets one weekly **freeze** that automatically protects a single missed day so the streak survives.
- **XP & gems:** every quiz awards XP (base + perfect bonus + daily-goal bonus + streak-milestone bonus) and gems (2 per correct, 5 per quiz, bonus on level-up). XP only ever increases.
- **Levelling system:** total XP maps to named tiers (a StarQuest-style progression), with a celebration overlay on level-up.
- **Leaderboard:** XP-ranked over three windows — daily, weekly (summed from `daily_challenges`), and all-time (`users.xp`) — every row carrying the user's equipped avatar.
- **Avatar + shop:** a customisable **monkey mascot** (skin tones + outfit) rendered consistently across the navbar, home, profile, leaderboard and shop preview.
- **History & saved:** every attempt is stored with its full question blob so it can be **retaken** exactly (retakes don't re-award XP); "saved" quizzes are attempts the user named, replayed with the same filters.

### 3.5 Authentication
Two sign-in paths converge on one login token (a **JWT** — a signed token the server hands the browser at login and checks on every request, so users stay logged in without re-entering their password): email/password (bcrypt-hashed) and **Google OAuth** (Google Identity Services token verified server-side). If a Google login matches an email that already signed up with a password, the accounts are **merged** onto one row rather than duplicated, so a student can use either method for the same account. The teacher flag is carried in the token so teachers route straight to their dashboard. Tokens were set to a **30-day lifetime** so returning students aren't logged out mid-use.

### 3.6 Teacher dashboard
Teacher accounts (`is_teacher = true`, set in the DB) get a read-only `/api/teacher/overview`: a week-at-a-glance (active students, total quizzes, class average, pass rate, inactive count), the weakest topics across the class, inactive students, and a per-student consistency view. It's guarded by `require_teacher()`, which rejects non-teachers with a 403.

### 3.7 Deployment / "Operate"
The app is deployed as: **front end on Cloudflare Pages**, **backend on Render (Singapore region)**, **MySQL on Railway**, with diagram images served from Google Drive through the backend's cached proxy. All environment-specific values (DB credentials, `JWT_SECRET`, Google client ID, service-account JSON, `PUBLIC_BASE_URL`, CORS allow-list) come from platform environment variables, so no secrets live in the repo. Health checks, single-replica config, and CORS origins are wired through `render.yaml` / `railway.toml`. Ooka is currently in a **test phase, rolled out to ~22 students** for feedback before a wider release.

---

## 4. The Hardest Problems I Faced

Pick 2–3 of these to write up in depth for the report — depth on a couple of hard problems (with what you tried, what failed, and how you solved it) is what pushes the grade up. Each below is structured as *problem → attempts → resolution* so it's ready to expand.

### 4.1 Reliable diagram extraction from exam papers *(the biggest one — weeks 2–6)*
**Problem:** Pulling the *text* out of papers was easy; pulling the **diagrams** out cleanly was not. Cropped images were inconsistent — sometimes cutting off part of a diagram, sometimes catching stray text or characters, often full of noise. And every Paper 1 is laid out differently, so a crop that worked on one paper broke on the next.
**Attempts:** I iterated for weeks on `extract_docx.py`, adding and tuning crop parameters. I set myself a concrete acceptance test — **five clean extractions in a row with zero issues** — and repeatedly failed to reach it because each new paper format exposed a new edge case. For Paper 2 answer diagrams (the worst offender — text kept getting cut out) I even tried having **Claude recreate the diagrams** from scratch rather than crop them.
**Resolution:** I scrapped diagram-recreation as too unreliable and instead extracted the **whole question-and-answer as a single image**, which sidestepped the cut-off-text problem entirely. For question diagrams I tuned the pipeline through a deliberately tedious test campaign across a wide range of real paper formats — no two papers are laid out the same, so the goal was robustness across variety rather than a fixed count — until it reliably hit the five-clean-extractions-in-a-row bar, then handed the finished Paper 1 extraction to my supervisor. The final bank spans ~130 papers across five levels, which is the practical evidence that the pipeline generalises beyond any single layout.

### 4.2 Question splitting without a big model
**Problem:** Splitting a full paper into individual, correctly-bounded questions is deceptively hard.
**Attempts:** Local LLMs via GPT4All (Llama 3 8B Instruct and others) — the smaller models couldn't segment reliably.
**Resolution:** Moved the splitting/classification to **Claude**, which handled boundaries, difficulty and SIO matching consistently. Lesson: match the tool to the task — a small local model was the wrong economy here.

### 4.3 Serving the right diagram image every time
**Problem:** Sheet cells reference images by *filename*, but the browser needs a real Drive URL, and filenames drift (case, extension, trailing suffixes like `-Setup`). Large Drive folders also silently capped at 1,000 files.
**Resolution:** A startup **`file_map`** indexes every Drive file by exact name, lowercase, and name-without-extension; a `/api/image/{id}` **proxy** resolves references (with extension-guessing and prefix-match fallback), streams the bytes, and **caches** them in memory (FIFO, 1,000 entries) so each image hits Drive at most once. I also fixed the 1,000-file cap by paginating through `nextPageToken`.

### 4.4 The rank → XP pivot *(a design failure I corrected)*
**Problem:** I first built a **rank system** using Singapore grade bands (F9…A1) per subject. On reflection with my supervisor, this was the wrong fit for what Ooka is trying to do: a rank that looks like a grade tells a student "how smart you are," and it made the app feel like just another report card. It also risked *dropping* after a bad day, which punishes exactly the behaviour we want to encourage — showing up to practise.
**Resolution:** We pivoted to an **XP levelling system** that rewards **consistency, not correctness or level**. No matter which level a student is working at or how many they get right, steady daily practice moves them up, and XP only ever increases — so a bad day never sets a student back. This makes progression feel earned and encouraging rather than judgemental, which is the model every successful learning app (Duolingo, Khan Academy) uses. The old rank tables were left in place but orphaned, in case per-subject stats are revived later.

### 4.5 The monkey-avatar rebrand *(HabitGo → Ooka)*
**Problem:** The original avatar was an emoji-based "Mr Potato Head" the user assembled from hats/glasses/hands. When we rebranded to Ooka around a **monkey mascot**, real SVG/PNG assets wouldn't sit correctly on the character — they were designed at fixed sizes and didn't reflow, and hardcoded positions drifted when the base art changed.
**Attempts / resolution:** I processed the monkey art programmatically (Python + PIL/scipy): flood-fill background removal, a 2px erosion to kill the white anti-alias "halo" that glowed on dark backgrounds (while carefully preserving the enclosed white eye-glints), and HSV recolouring to generate six skin tones. Positioning was made **percentage-based** off the avatar size so the same primitive renders cleanly from a 28px nav chip to a 128px profile hero, and the outfit is rendered *outside* the clipped disc so sleeves aren't cut off. The result renders consistently across all five surfaces from one `<Avatar>` component.

### 4.6 Streak edge cases
**Problem:** Streaks and freezes have nasty edge cases — timezone drift, multi-day gaps, freeze double-consume, and duplicate same-day submissions.
**Concrete bug (from the code's defences):** the freeze could be **double-consumed** — a user who missed a day would have the freeze correctly protect it, but further activity could try to spend the same freeze again, or a freeze could fire on a day the student had actually completed.
**Resolution:** `daily_challenges` has a `UNIQUE(user_id, subject, date)` so multiple submissions on the same day update one row instead of stacking; the streak only increments the *first* time the goal flips to passed; a `freeze_used_date` records exactly which day a freeze protected so it can't be consumed twice; the code checks the day was *not* already passed before spending a freeze; and gaps larger than the freeze budget explicitly reset the streak rather than silently surviving.

### 4.7 Making the app fast (Week 22 performance work)
**Problem:** As the question bank and image set grew, the naive paths were far too slow — re-reading Google Sheets on every quiz request and re-fetching every diagram from Drive on every view would have made the app crawl and burned through Google API quotas.
**Resolution — the optimisations built in:**
- **In-memory question cache:** the whole bank is loaded from Sheets **once** into memory and reused for the process lifetime, so a quiz request is served from RAM instead of a slow API round-trip.
- **In-memory image cache:** the `/api/image` proxy caches diagram bytes (FIFO, 1,000 entries), so each image is fetched from Drive at most **once** per process instead of on every question view.
- **Batch Sheet fetch:** every level tab is pulled in a **single** `batchGet` call rather than one call per tab.
- **Drive file-map + pagination:** filenames are indexed once at startup (and paginated past the 1,000-file cap) so image lookups are dictionary hits, not live Drive searches.
- **Filename-based image URLs:** images are addressed by filename rather than a volatile Drive ID, so re-uploading a diagram doesn't break existing references.
- **Lazy-loaded frontend routes:** heavier pages (settings, teacher dashboard, history) are code-split with `lazy()` so the initial app loads a smaller bundle.
- **Single-replica by design:** because those caches live in process memory, the service runs as one replica, keeping cache behaviour correct and predictable.

**Deployment angle (also "Operate"):** getting three platforms (Cloudflare Pages, Render, Railway) to talk to each other — CORS allow-lists, Google OAuth authorised origins, and building absolute image URLs via `PUBLIC_BASE_URL` — while keeping the in-memory caches valid. `[OPTIONAL FILL: any felt before/after — e.g. "quiz load went from a noticeable pause to near-instant" — if you remember it.]`

---

## 5. Status of gaps

Almost everything is now filled from your answers and the codebase:

1. ✅ **Metrics** — ~130 papers (G3 ~91, Pure ~24, G2 ~7, G1 ~3, P6 Math ~5). *(in §2.1 / §3)*
2. ✅ **Users** — test phase, ~22 students. *(§3.7)*
3. ✅ **Extraction quality** — framed as robustness across varied formats + the ~130-paper spread. *(§4.1)*
4. ✅ **Rank→XP** — consistency over grades; XP never drops. *(§4.4)*
5. ✅ **Streak bug** — freeze double-consume, reconstructed from the code's defences. *(§4.6)*
6. ✅ **JWT / login token** — 30-day lifetime, explained in plain language. *(§3.5)*
7. ✅ **Week 22 optimisation** — full list reconstructed from the code. *(§4.7)*
8. ⏳ **Screenshots** — you'll send these; I'll place them as figures.

**Still to gather from you (the non-technical sections):**
- **Problem framing (point 1):** in your own words, the gap Ooka solves and why Curious Lab wanted it — I have a technical version, but your voice here is good.
- **Results/impact (point 4):** any feedback from the 22 test students, and how the work helped Curious Lab.
- **Team (point 5):** how you worked with Marcus (supervisor) — what he directed vs. what you decided/proposed (e.g. the rebrand, the rank→XP pivot, the syllabus restructure).
- Plus the company / industry / careers / reflection sections for the full report.
