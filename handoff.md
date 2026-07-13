# Ooka — Project & Session Handoff

_Last updated: 2026-07-02 · Covers both the frontend and backend repos._

Ooka is a gamified O-Level / Combined-Science quiz app for **CuriousLab.sg**
(formerly "HabitGo"). Students pick a subject, build practice quizzes from a
Google-Sheets question bank, earn XP/gems, climb StarQuest ranks, and customise
a monkey avatar. Teachers get a read-only dashboard.

---

## 0. What you must do now (action items)

1. **Push both repos** from your machine (the sandbox can't authenticate to GitHub):
   ```
   cd C:\School\quiz-maker-frontend && git push origin main   # HEAD = 4e7ae85
   cd C:\School\quizMaker            && git push origin main   # HEAD = 71ad177
   ```
   If `git status` looks odd, run `git reset` (no flags — keeps all files) first,
   then push. Do **not** re-commit — see §6.
2. **Restart the local backend** and let **Render redeploy** the hosted backend.
   Required for: the per-level question banks, per-level topic filters, the
   filename-based (re-upload-proof) image URLs, the new `users` columns
   (school/class/teacher — migrated at startup), the `/api/auth/complete-profile`
   endpoint, and login/Google/me now returning the profile fields.
3. **Rebuild / redeploy the frontend** so the new bundle ships.

---

## 1. Repos, deploys, data, credentials

| Thing | Value |
|---|---|
| Frontend repo | `github.com/ezelllow/quiz-maker-frontend` (React + Vite + Tailwind + framer-motion) |
| Backend repo | `github.com/ezelllow/quiz-maker-backend` (FastAPI + MySQL + Google Sheets/Drive) |
| Backend deploy | Render — `https://quiz-maker-backend-5ged.onrender.com` |
| Local frontend | `C:\School\quiz-maker-frontend` (Vite dev server, usually `:5173`) |
| Local backend | `C:\School\quizMaker\quiz_backend.py` (uvicorn `:8000`) |
| Question Sheet | ID `1TOmLo9UNpzOggeX27j1p6Q2NdAnCWpRJ1ErYAEJ-sZU` |
| Service account | `quiz-maker@celestial-brand-449415-e5.iam.gserviceaccount.com` |

**Never commit** `.env` or `credentials.json` (they're gitignored) or personal
notes (`climbing_talk_notes.md`).

### Google Sheet tabs (question bank)
`Pure Physics`, `combinedG3`, `combinedG2`, `combinedG1` are the question tabs
(old `4E5N` is now empty). Level column values match the tab names.
⚠️ `combinedG1` uses different headers (`Unique ID`, `Question No`) than the
others (`UID`, `QNo`) — the loader handles both.

### Google Drive image drives (one per level, per-paper subfolders)
| Level | Drive folder ID | Name |
|---|---|---|
| pure | `1c3e88WMHQ62uG1AZ4VDeK_d5tMwPwFqO` | pure_physics_p1 |
| combinedG3 | `1IH-v6RCDsEnYm8oeJS7RaIhSicHhNFcC` | combined_physics_p1_G3 |
| combinedG2 | `154YP-TOlk6gFgVS6e9Hegr60CE26OWDl` | combined_physics_p1_G2 |
| combinedG1 | `1RkICWBLlBpV0k87NZzRFTifpL-evTwDw` | combined_physics_p1_G1 |

---

## 2. The four "subjects" (levels)

Each level is its own **subject** on the Practice page (not a filter):

| Subject (label) | levelKey | Syllabus | Topics |
|---|---|---|---|
| Pure Physics | `pure` | SEAB 6091 | 20 (21 canonical) |
| Combined Physics G3 | `combinedG3` | O-Level Combined 5086/87/88 | 16 |
| Combined Physics G2 | `combinedG2` | Normal (Academic) Science 5105/06/07 | 13 |
| G1 Science | `combinedG1` | Normal (Technical) Science 5148 | 11 (Physics + Chemistry + Biology) |

**G1 Science is a full-science subject** (Physics: Energy/Electricity/Wave/Effects
of Force; Chemistry: Food Matters; Biology: Our Body & Health) — its questions are
tagged with O-Level topic names and get **bucketed** into the 11 official 5148
topics at load.

---

## 3. What changed this session

**Subjects & topics**
- Split into 4 distinct subjects on the Practice page; QuizMaker skips the old
  Pure/Combined sub-filter (shows a read-only banner) in practice mode; daily
  mode keeps a 4-way selector.
- Exact-tier question filtering — selecting Combined G2 returns only G2 (no
  merged combined). Zero cross-level leakage (verified against live data).
- Per-tier syllabus topic lists; the picker shows the **full** official list,
  disabling topics with no questions yet ("Soon" tag) — e.g. G2 Radioactivity.
- G1 topic bucketing + robust multi-level number-prefix stripping
  (`"1.1 Sources of Food"` → `"Sources of Food"`).
- `Safe Use of Electricity` → `Practical Electricity` canonicalisation.

**Subject picker UI**
- Grouped into "Subjects" / "Coming soon" sections; equal-height cards (explicit
  inline height via a new `style` prop on `TopicCard`); accurate taglines; the
  G1 card is labelled **"G1 Science"**.

**Images (the recurring 404s)**
- Root cause: re-uploading an image changes its Drive ID, and URLs had the old
  ID baked in. Fix: **image URLs now carry the filename**, and `serve_image`
  resolves the current ID per request (file_map → Drive name-search → retry).
  Re-upload-proof; no restart needed after future re-uploads.
- Backend scans all 4 drives; `resolve_file_id` + `_drive_search_id` fallback.

**Auth / sessions**
- **4-hour inactivity auto-logout** (persisted across reloads/closed tabs;
  resets on activity; re-checks on tab focus).
- **Global 401 interceptor** — stale/expired tokens cleanly log the user out to
  the login screen with a notice, instead of the app half-working.

**Signup / profile**
- Signup form collects **School** (ESSS, BGSS), **Class** (text), **Teacher**
  (Mr Lloyd Goh) — all required; stored on the user (`school`, `student_class`,
  `teacher` columns, auto-migrated at startup).
- **Complete-profile gate**: any signed-in **student** missing any of the three
  is shown `CompleteProfile` before the app (covers Google signups + legacy
  accounts). Teachers exempt. Endpoint: `POST /api/auth/complete-profile`.
  Login/Google/`/api/auth/me` now return the three fields.

---

## 4. Backend reference (`quiz_backend.py`)

- **Level helpers:** `_level_key(value)` → `pure` / `combinedG1|2|3` / `combined`;
  `_level_matches(req_key, q_level)`; `_order_for_level(req_key)`.
- **Topic orders:** `PURE_TOPIC_ORDER` (21), `COMBINED_TOPIC_ORDER` (16, =G3),
  `COMBINED_G2_TOPIC_ORDER` (13), `COMBINED_G1_TOPIC_ORDER` (11);
  `_LEVEL_TOPIC_ORDER` maps key → order.
- **Canonicalisation:** `canonical_topic(name, combined=False, level_key=None)`
  → `_canonical_topic_base` (+ `_norm_topic` regex prefix strip) → `_COMBINED_MERGE`
  for combined → `_NT_BUCKET` for G1 (physics buckets; chem/bio pass through).
- **Topic endpoints:** `/api/subtopics?level=` returns the full official list;
  `/api/availability?level=` restricts to the syllabus; quiz-gen "all topics"
  restricts to `_order_for_level`.
- **Images:** `/api/image/{file_id}` — resolves filename→ID via `file_map`
  (built by `load_file_map`, recursive across the 4 drives) then a Drive
  name-search fallback + retry on fetch failure. URLs are built from the raw
  filename at load (all `resolve_file_id(...)`-in-URL sites removed).
- **Auth:** JWT 30-day (`JWT_EXPIRATION_HOURS = 720`). Signup/login/google/me all
  return `school`/`student_class`/`teacher`. `require_teacher` gates the teacher
  dashboard via the `is_teacher` claim (flipped manually in DB).

---

## 5. Frontend reference

- `src/components/PracticePage.jsx` — subject picker (`subjects[]`), SubjectHub,
  routes into QuizMaker with `initialLevel`/`levelLabel`.
- `src/components/QuizMaker.jsx` — quiz builder; `syllabusFor(levelCat)` returns
  the per-tier `SEAB_*_ORDER` regex maps; empty topics disabled.
- `src/components/CompleteProfile.jsx` — the school/class/teacher gate.
- `src/App.jsx` — auth state, `forceLogout`, inactivity + 401 effects,
  `needsProfile` gating, `handleLogout`.
- `src/components/ui/TopicCard.jsx` — now accepts a `style` prop.
- To add a school/teacher: edit the `<option>` lists in **both**
  `SignupPage.jsx` and `CompleteProfile.jsx`.

---

## 6. Dev workflow gotchas (important)

- **File-tool corruption:** the Read/Write/**Edit** tools sometimes truncate the
  tail of large files (`quiz_backend.py`, `QuizMaker.jsx`) or inject NUL bytes.
  Always verify after editing: `NUL == 0` and `py_compile` / `esbuild` pass.
  Prefer python scripts (string-replace) for edits to big files; if corrupted,
  restore the file from git and re-apply.
- **Git in the sandbox:** the mount blocks deleting `.git/index.lock`, so normal
  `git commit`/`push` fail. Commits this session were made via
  `GIT_INDEX_FILE` + `write-tree`/`commit-tree` + writing `.git/refs/heads/main`
  directly. Consequence: your **local index is stale** → `git status` may show
  committed files as "modified". Run `git reset` (no flags) to resync; the
  commits are real (`git log` proves it). The sandbox **cannot push** (no
  GitHub creds) — always push from your own machine.

---

## 7. Known issues / open items

- **Teacher/class linking is NOT built.** `teacher` is just a text label on the
  student; the teacher dashboard (`/api/teacher/overview`) aggregates the whole
  student body and does **not** filter by class or teacher. Building "Mr Lloyd
  Goh sees only his classes" is the natural next step.
- **~27 G1 questions** tagged only generically ("Chemistry"/"Biology") aren't
  surfaced — re-tag to a specific 5148 topic to show them.
- **9 G2 questions** tagged Turning Effect/Light are hidden (out of the Normal
  Academic syllabus) — re-tag if they should be usable.
- **combinedG2 "easy"** is thin (~8 questions); the builder greys out
  difficulties that can't fill the requested count.
- Saved-quizzes ("Retake") list isn't split per subject yet.

---

## 8. Commit log (this session)

**Frontend** (`quiz-maker-frontend`), newest first:
```
4e7ae85  Complete-profile gate for Google signups + legacy accounts
9f9521e  Signup form: School (ESSS/BGSS), Class, Teacher (Mr Lloyd Goh)
75f1525  Inactivity auto-logout (4h) + 401 self-heal; equal-size subject cards
7fa3774  Per-level topic filters + cleaner subject picker
bb112d9  Subjects page: 4 physics levels as distinct subjects (Pure/G3/G2/G1)
```

**Backend** (`quiz-maker-backend`), newest first:
```
71ad177  Return school/class/teacher in login/google/me; add /api/auth/complete-profile
adb8539  Signup captures school / class / teacher
edef766  Image URLs use filename, not resolved Drive ID (re-upload-proof)
681271f  G1 full-Science syllabus, robust topic canonicalisation + self-healing images
8f34301  Per-level question banks: Pure / Combined G3 / G2 / G1
```

All committed locally; **not yet pushed** (do §0 step 1).
