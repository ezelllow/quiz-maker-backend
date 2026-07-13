# HabitGo — Technical Architecture

> A single-file physics-practice platform for Singapore O-Level students.
> Built by CuriousLab. This document explains how every subsystem works.

---

## 1. Tech Stack

### Frontend — `quiz-maker-frontend/`
| Layer | Choice | Notes |
|---|---|---|
| Framework | **React 19** | Function components + hooks, no class components |
| Bundler / dev server | **Vite 8** | ESM-first, dev server at `localhost:5173` |
| Styling | **Tailwind CSS 3.4** | Utility-first; custom theme in `tailwind.config.js`, CSS variables in `src/index.css` |
| Animation | **Framer Motion 12** | Reusable variants in `src/motion/index.js` (`ease.spring`, `ease.bouncy`, etc.) |
| Fonts | **Nunito** (body) + **Baloo 2** (display) | Loaded via Google Fonts `@import` |
| State | Local component state + `localStorage` | No Redux / Zustand. User state lives in `App.jsx` and is passed via props. |

### Backend — `quizMaker/quiz_backend.py`
| Layer | Choice | Notes |
|---|---|---|
| Framework | **FastAPI** | Single ~5000-line file (`quiz_backend.py`) |
| Server | **uvicorn** | Launched via `python quiz_backend.py`. Reload OFF to prevent schema-migration double-runs. |
| DB driver | **mysql-connector-python** | Thread-safe, connection-per-request pattern |
| Auth | **PyJWT** + **passlib\[bcrypt\]** | HS256 JWTs, 24-hour expiry |
| Google APIs | **google-api-python-client** | Reads Sheets + Drive via a service account (`credentials.json`) |

### Database — MySQL 8
Seven tables (see §2). Schema is created/migrated by `init_database()` at server startup — every `ALTER TABLE` is guarded by an `information_schema.COLUMNS` count check, so it's idempotent and safe to re-run.

### External storage
- **Google Sheets** — question bank (see §4).
- **Google Drive** — question diagram images. Backend proxies these via `/api/image/{file_id}`.

---

## 2. MySQL Schema

Seven tables, all with `ON DELETE CASCADE` from `users`.

### `users`
The identity table. Auth methods can coexist on one row (email/password AND Google login for the same account).
```sql
id                INT PK AUTO_INCREMENT
email             VARCHAR(255) UNIQUE NOT NULL
password_hash     VARCHAR(255)          -- NULL if google-only signup
google_id         VARCHAR(255)          -- NULL if email-only signup
name              VARCHAR(255)
avatar_url        LONGTEXT               -- data:image/... base64 or drive URL
xp                BIGINT DEFAULT 0
gems              BIGINT DEFAULT 0
daily_goal        SMALLINT DEFAULT 10
equipped          JSON                  -- {hat, glasses, accessory, frame, hands, legs}
test_day_offset   INT DEFAULT 0         -- QA/dev cheat for advancing "today"
is_teacher        BOOLEAN DEFAULT FALSE
created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```

### `quiz_attempts`
Every quiz — practice, daily, retake, placement — writes one row here.
```sql
id                    INT PK AUTO_INCREMENT
user_id               INT NOT NULL FK → users(id)
name                  VARCHAR(255)         -- user-chosen quiz name, nullable
difficulty            VARCHAR(50)          -- 'easy' | 'medium' | 'hard'
subtopic              VARCHAR(255)         -- ' · '-joined if multi-topic
score                 INT                  -- questions correct
percentage            INT
total_questions       INT
time_spent_seconds    INT
questions_data        LONGTEXT             -- JSON blob: full questions + user_answers
parent_attempt_id     INT NULL             -- if this is a retake, points to the original
quiz_type             VARCHAR(20)          -- 'practice' | 'daily' | 'placement'
attempted_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```
Indexes: `user_id`, `attempted_at`, `parent_attempt_id`.

### `streaks`
One row per user. Global (not per-subject).
```sql
id                     INT PK
user_id                INT UNIQUE FK
current_streak         INT DEFAULT 0
longest_streak         INT DEFAULT 0
last_qualified_date    DATE                 -- last day the user hit their daily goal
freezes_available      INT DEFAULT 1
freeze_last_granted    DATE
freeze_used_date       DATE                 -- what date a freeze protected
updated_at             TIMESTAMP
```

### `daily_challenges`
One row per `(user_id, subject, challenge_date)`. Accumulates progress across multiple attempts on the same day.
```sql
id                INT PK
user_id           INT FK
subject           VARCHAR(100)
challenge_date    DATE
score             INT                     -- correct answers accumulated today
total             INT                     -- total answers attempted today
percentage        INT
passed            BOOLEAN                 -- true once score ≥ daily_goal
attempts          INT                     -- how many quiz submissions today
xp                BIGINT                  -- XP earned today; drives daily/weekly leaderboards
created_at        TIMESTAMP
updated_at        TIMESTAMP
UNIQUE (user_id, subject, challenge_date)
```

### `user_rewards`
Shop purchases. `UNIQUE (user_id, reward_id)` prevents double-redemption.
```sql
id             INT PK
user_id        INT FK
reward_id      VARCHAR(64)      -- e.g. 'hat_wizard', 'frame_galaxy'
cost           INT              -- gems paid at purchase time
redeemed_at    TIMESTAMP
fulfilled_at   TIMESTAMP NULL   -- reserved for physical rewards (currently unused)
UNIQUE (user_id, reward_id)
```

### `user_subject_ranks` + `rank_history`
Legacy: the pre-XP-pivot rank system (see §9). `user_subject_ranks` holds current band per subject; `rank_history` appends a row on every rank change.
```sql
-- user_subject_ranks: current state
id, user_id, subject, rank_band, rank_score, placed_at, updated_at
UNIQUE (user_id, subject)

-- rank_history: append-only audit trail
id, user_id, subject, rank_band, rank_score, recorded_at
```

### Relationships (ER-lite)
```
users 1 ─── * quiz_attempts        (user_id)
users 1 ─── 1 streaks              (user_id, UNIQUE)
users 1 ─── * daily_challenges     (user_id + subject + date UNIQUE)
users 1 ─── * user_rewards         (user_id + reward_id UNIQUE)
users 1 ─── * user_subject_ranks   (user_id + subject UNIQUE)
users 1 ─── * rank_history         (append-only)
quiz_attempts (parent_attempt_id) ─→ quiz_attempts.id  (self-ref for retakes)
```

---

## 3. Deployment

> **[TO FILL IN — I don't have direct knowledge of your prod deploy targets. Here's the shape you should describe:]**

- **Railway** hosts: [e.g. the MySQL database + backend]
- **Render** hosts: [e.g. the FastAPI backend service]
- **CloudFront** serves: [e.g. the static frontend build (Vite `dist/` output) + assets]
- **DNS**: [Cloudflare? Route53? Domain?]
- **Environment variables**:
  - `JWT_SECRET`, `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
  - `SPREADSHEET_ID`, `SHEET_NAMES`, `P6_MATH_SPREADSHEET_ID`
  - `QUESTION_FOLDER_ID` (Google Drive folder for images)
  - `GOOGLE_CLIENT_ID` (for OAuth)
  - `PUBLIC_BASE_URL` (used to construct image URLs sent to frontend)

The pattern is standard: managed DB service → hosted API → static-asset CDN in front of the SPA.

---

## 4. Question Data Flow — Sheets vs MySQL

**Questions live in Google Sheets, NOT in MySQL.** MySQL only stores user data (accounts, attempts, streaks, gems). This is a deliberate design choice — content editors can edit Sheets directly without touching a DB.

### Loading path
`QuestionCache._load_questions_unlocked()` in `quiz_backend.py`:

1. On startup (or on demand), the backend calls the Sheets API via a service-account JWT.
2. **Batch fetch**: `spreadsheets().values().batchGet(spreadsheetId, ranges=SHEET_NAMES)` grabs every configured tab in one API call.
3. **Fallback**: if any tab name is stale (deleted/renamed), the batch fails. The code then falls back to per-tab `.get()` calls and silently skips missing tabs — so one broken tab can't take down the whole quiz endpoint.
4. **P6 Math sheet** is a separate workbook, fetched independently and appended.
5. Rows are merged: header from the first non-empty tab wins; other tabs' headers are skipped. If a tab has the same column *names* in a different *order*, rows are re-mapped by header name (this is how the P6 workbook merges with the physics tabs cleanly).
6. Each row becomes a `Question` object stored in `cache.questions` (a Python list in memory).
7. Image references in the rows are resolved via `cache.file_map` (see §14).

### Sync model
- **In-memory cache**: `self.questions = []` is populated once and reused until the process restarts.
- **Forced refresh**: `cache.load_questions()` is idempotent — subsequent calls no-op if already loaded.
- **No real-time sync**: if you edit the Sheet, you have to restart the backend (or hit a manual refresh endpoint if you build one). This is a known constraint — teachers are used to the "edit → restart" cycle.
- **No import script**: the Sheet is the source of truth. There's no CSV import into MySQL.

### Why sheets and not MySQL for questions?
- Content editors (teachers) can edit questions in Google Sheets with rich features (comments, revision history, easy sharing) without needing DB access.
- Diagrams live in Drive (also editable by teachers), referenced by `IMAGE:filename.png` in Sheet cells.
- The tradeoff: no free-text search, no complex queries. But question filtering (subject/topic/difficulty) happens in Python after the load, which is fast enough for ~1000 questions.

---

## 5. Authentication

Two paths, one JWT.

### Signup — `POST /api/auth/signup`
- Payload: `{name, email, password}`
- `password_hash = bcrypt.hash(password)` via passlib (`CryptContext(schemes=["bcrypt"])`)
- Inserts a new `users` row with `password_hash` set and `google_id NULL`
- Returns a JWT immediately (auto-login)

### Login — `POST /api/auth/login`
- Payload: `{email, password}`
- Looks up user by email, verifies `passlib.verify(password, password_hash)`
- Returns JWT on match

### Google OAuth — `POST /api/auth/google`
- Frontend uses Google Identity Services (GIS) — the "Sign in with Google" button gives us an `id_token`
- Backend verifies the token against Google's public keys (`google.oauth2.id_token.verify_oauth2_token`)
- Extracts email + name + picture from the verified token
- **Account merging**: if a user already exists with that email (from a prior password signup), we set `google_id` on that existing row — same account, second sign-in method
- If it's a brand-new email, a new `users` row is created with `password_hash NULL` and `google_id` populated

### JWT structure
```json
{
  "user_id": 42,
  "email": "student@example.com",
  "is_teacher": false,
  "exp": <utc timestamp + 24h>,
  "iat": <utc timestamp>
}
```
Signed with HS256 using `JWT_SECRET`. `is_teacher` is baked in so the frontend can route straight to the teacher dashboard without a second round-trip to `/api/auth/me`.

### Token verification
Every protected endpoint calls `verify_jwt_token(Bearer <token>)`. Expired tokens (`ExpiredSignatureError`) and invalid tokens both return `None` → 401. The frontend stores the JWT in `localStorage.auth_token`.

### Known limitation
**Tokens expire after 24 hours** and the frontend doesn't auto-refresh. Users returning after >24h see cached UI (stale `localStorage.user` object) with 401s on every API call. The fix would be a global 401 handler that forces logout + redirect — worth adding.

---

## 6. Quiz Generation

### User selections (frontend)
- **Physics level**: Pure (SEAB 6091) OR Combined (SEAB 5086/87/88) → filters the topic list
- **Topics**: 0-3 checkboxes from an official-syllabus-locked list (see §17)
- **Difficulty**: Easy / Medium / Hard
- **Count**: 10, 15, or 20 (practice mode allows 1-100)

### Availability preview
Before submitting, the frontend queries `/api/availability?level=pure` which returns:
```json
{ "Kinematics": { "easy": 12, "medium": 24, "hard": 8 }, ... }
```
Difficulty tiles are greyed out live if the selected topics can't cover the selected count at that difficulty (`difficultyAvailable()` in QuizMaker.jsx mirrors the backend allocation math).

### Auto-snap
If a user picks Medium for 20 questions across 2 topics and only one topic has 10 medium questions, the picker **auto-snaps** to the nearest valid difficulty (`nearestValidDifficulty()`), preferring the easier side on ties. Users see a visible pop animation on the snapped tile — never silently upgraded.

### Backend generation — `POST /api/quiz`
1. Loads `cache.questions` (in-memory list from Sheets)
2. Filters by subject + level + difficulty
3. Splits `count` evenly across chosen topics: `perTopic = ceil(count / nTopics)`
4. Random-samples `perTopic` questions from each topic's pool
5. Resolves any `IMAGE:` references to `{PUBLIC_BASE_URL}/api/image/{file_id}` URLs
6. Returns `{questions: [...]}`

### What happens when no questions match
- Empty pool → `count` requested > pool size → backend returns 400 with a message
- Frontend's availability check should prevent this from reaching the backend, but the backend defends anyway
- Auto-snap usually catches "no medium available, fall back to easy" cases before submission

---

## 7. Streak System

### Core loop
- A user's daily goal is `users.daily_goal` (default 10 correct answers).
- Each quiz submission calls `_award_streak_day()` which:
  1. Computes today's date (respects `test_day_offset` for QA)
  2. Reads the existing `streaks` row (or creates one with defaults)
  3. If today's `daily_challenges` row shows the goal has been hit AND today > `last_qualified_date`, increments `current_streak` and sets `last_qualified_date = today`
  4. Updates `longest_streak = max(current, longest)`

### Streak Freeze
Users get **1 freeze weekly**. Freezes protect against missed days:
- If a user skips exactly ONE day (gap between `last_qualified_date` and today = 2 days), the freeze auto-consumes and the streak survives. `freeze_used_date` records which day was protected.
- Freezes bank up to a cap (`freeze_cap`, default 2).
- New freezes are granted weekly (`freeze_last_granted` tracks when).

### Edge cases that needed troubleshooting

> **[TO FILL IN with your Week-X debugging stories. Here are the categories the code shows evidence of:]**
>
> 1. **Timezone drift** — early versions probably used `NOW()` server time; had to lock to a consistent timezone (probably SGT). Grep the code for `datetime.now()` vs `datetime.utcnow()` to see where the timezone semantics live.
> 2. **Multi-day gaps** — if a user misses 2+ days, freeze can only protect one. The `_award_streak_day()` code has an explicit "gap bigger than freeze budget → reset" branch (line ~4368).
> 3. **Freeze double-consume** — once a freeze protects day N, `freeze_used_date` records it so subsequent same-day activity doesn't try to consume another.
> 4. **Freeze on the qualifying day** — you can't freeze a day you actually completed. The code checks `passed` on the daily_challenges row before consuming.
> 5. **Retake / duplicate submits** — daily_challenges has `UNIQUE (user_id, subject, challenge_date)`, so multiple submits on the same day update the same row instead of stacking. Streak increment only fires on the FIRST time `passed` flips true.

---

## 8. Daily Quiz

### Selection
The daily challenge is NOT a fixed 10-question set. It's a **running counter**: keep answering questions today until you've hit the daily goal (default 10 correct).

- Frontend routes to `QuizMaker` in `mode="daily"` (vs `mode="practice"`)
- Question generation is the same as normal quizzes (§6) — same filters apply
- Each submission adds to today's `daily_challenges.score`; once `score >= daily_goal`, `passed = TRUE` and the streak fires

### Why "accumulate correct answers" instead of "one 10-Q quiz"?
Grants flexibility: a user can do a 3-question quick round, then a 5-question round later, and it still counts. Also lets teachers make quizzes any length without breaking the daily flow. The frontend shows "6 / 10 correct — keep going" progress.

### Rewards on completion
- **Streak awarded** (`+1 current_streak`) if today's goal was newly hit
- **Gems**: 2 per correct + 5 per quiz + 50 on rank-up
- **XP**: base + perfect bonus + daily-goal bonus + streak-milestone bonus (all computed in `_award_xp_for_quiz()`)

### Once done for today
Frontend's "Bonus Practice" button routes to `mode="practice"` — no XP, no gems, just drilling.

---

## 9. Rank → XP Pivot

### The old rank system
`user_subject_ranks` + `rank_history` + `RANK_BANDS = ["F9", "E8", "D7", "C6", "C5", "B4", "B3", "A2", "A1"]` — Singapore exam grade bands.

Users were given a rank per subject based on `rank_score` (recent performance metric). The system displayed grades like "You're at B3 in Physics" and awarded rank-up moments.

### Why it failed

> **[TO FILL IN with the specific reasons — I don't have the historical thread. Common reasons systems like this fail:]**
>
> - **Rank stagnation**: users hit their skill ceiling and stopped seeing progression. B3 for weeks → app feels stale.
> - **Regression pain**: doing badly one day could DROP your rank, which felt punishing. Users avoided practicing when tired to protect their band.
> - **Subject silos**: separate rank per subject meant no unified sense of "am I improving overall?"
> - **Not gamified enough**: a single letter grade doesn't hit the same dopamine as a growing XP bar.
> - **Grading semantics were confusing**: is B3 above or below C5? Which is better, higher or lower in SG grading? Doesn't map cleanly to "progress".

### Why XP levelling is better
The current system uses `users.xp` (a monotonically-increasing BIGINT). Reasons this works:

- **Never goes down**: users can only gain XP. Bad practice sessions cost nothing.
- **Continuous progression**: the XP bar always fills, tier changes always feel earned.
- **Universal metric**: one number spans all subjects, all difficulties.
- **Level tiers give flavor**: Rookie → Pilot → Navigator → Star Admiral etc. give named milestones without the semantic baggage of grade letters.
- **Compatible with leaderboards**: SUM XP over a period ranks users cleanly.
- **Behaviorally sound**: Duolingo, Khan Academy, Codecademy, every successful learning app uses XP not grades. The evidence was overwhelming.

### Implementation
- `_award_xp_for_quiz()` computes: `base = correct * 12` + perfect bonus + daily-goal bonus + streak-milestone bonus
- `compute_rank(xp)` maps total XP to a tier struct (`tier_name`, `tier_icon`, `xp_next`, `next_name`)
- Rank-up detection fires the `RankUpOverlay` celebration on the results screen
- `user_subject_ranks` + `rank_history` tables still exist — orphaned but not dropped, in case we revive per-subject stats later

---

## 10. Statistics

### Recorded per quiz
Every submit stores in `quiz_attempts`: score, percentage, total_questions, time_spent_seconds, questions_data (JSON blob with the actual questions + user_answers), difficulty, subtopic, quiz_type.

### Computed at query time
- **Overall accuracy** = `SUM(score) / SUM(total_questions)` across all attempts
- **First-attempt accuracy** = same but only counting attempts where `parent_attempt_id IS NULL` (excludes retakes)
- **Current streak / longest streak** = from `streaks` table
- **Weekly XP / all-time XP** = `daily_challenges.xp` sum for daily/weekly leaderboards, `users.xp` for all-time

### Displayed
- **Profile page**: streak, longest, accuracy (as CountUp animated numbers)
- **Dashboard**: per-topic breakdown, difficulty distribution, streak calendar
- **Home**: streak, rank tier, XP progress bar, weekly strip
- **History**: full attempt list with retake buttons
- **Teacher dashboard**: class-level aggregates (see §15)

---

## 11. History and Saved

### History
Every quiz attempt goes into `quiz_attempts` with the full questions blob stored as JSON in `questions_data`. The frontend History page (`/history`) lists all attempts newest-first.

**Retake flow**:
- User taps Retake on a past attempt
- Frontend calls `GET /api/history/{attempt_id}/quiz`
- Backend reads `questions_data` from that row, parses it, returns the same questions
- QuizMaker loads them in `isRetaking=true` state
- On submit, the new attempt writes a new row with `parent_attempt_id = original_attempt_id`
- `quiz_type = "practice"` on retakes so they never grant XP/gems or affect streak (already earned once)

### Saved
"Saved" quizzes are a filter over `quiz_attempts`: attempts where the user gave a `name` at submission time. This is the "save with the filters used" — `name` + `difficulty` + `subtopic` + `total_questions` all stored on the row, so re-taking replays the exact same structure. It's not a separate "template" table; the attempt row IS the template.

**Structural meaning**: `quiz_attempts.name IS NOT NULL` means the user chose to save this quiz's shape (probably for repeated drilling). Backend query for the Saved page:
```sql
SELECT id, name, difficulty, subtopic, total_questions, attempted_at
FROM quiz_attempts
WHERE user_id = %s AND name IS NOT NULL AND parent_attempt_id IS NULL
ORDER BY attempted_at DESC
```

---

## 12. Avatar System

### The Mr Potato Head model
Every avatar is a `<Avatar>` React primitive that composes:

- **Base circle** (photo from `users.avatar_url` OR a gradient with the user's initial)
- **Frame** — CSS ring around the photo (default is cyan `#34B6F0`; wearables can override with gold, rainbow, fire, or galaxy)
- **Hat** — emoji floating above the circle, slight rotation
- **Glasses** — emoji on the eye line
- **Accessory** — emoji as bottom-right corner badge
- **Hands** — TWO emoji, one on each side, mirrored (Mr Potato Head arms)
- **Legs** — TWO emoji at the bottom, side by side, same orientation

All wearables are looked up in `WEARABLES_REGISTRY` (in `src/components/ui/Avatar.jsx`) by their catalogue ID. Positions and sizes are computed as percentages of the circle size, so the same primitive scales cleanly from 28px (nav chip) to 128px (profile hero).

### Why assets didn't fit initially

> **[TO FILL IN with the exact story — likely candidates:]**
>
> - **Original approach**: real SVG assets layered on a monkey-shaped base. SVGs were designed at fixed pixel dimensions and didn't reflow when the avatar shrank/grew across screens.
> - **Positioning was hardcoded**: hat X/Y was designed for a specific base image, so swapping the base moved the hat off-center.
> - **File count**: proper SVG assets for every wearable × slot × size combo would balloon into hundreds of files.

### How it was solved
- **Emoji as wearables**: the current system uses emoji (Unicode), which:
  - Scale infinitely with `font-size`
  - Work in every browser without asset delivery
  - Are pre-designed to render at any size
- **Percentage-based positioning**: hat sits at `top: -s.hat * 0.55`, glasses at `top: s.circle * 0.30`, etc. All positions are functions of the circle size, so scaling works.
- **Per-size geometry table**: the `SIZE` object in `Avatar.jsx` defines `circle`, `hat`, `glasses`, `accessory`, `hands`, `legs`, `font` for six sizes (xs/sm/md/lg/xl/hero). Each wearable knows how big to render at each avatar size.
- **Frames via CSS `box-shadow`/`background`**: the ring style is generated in JS (`FRAME_STYLE.default`, `.gold`, `.rainbow`, `.fire`, `.galaxy`) — rainbow uses `conic-gradient`, galaxy uses another cosmic conic-gradient. No image assets needed.

### Consistent rendering everywhere
The `<Avatar>` primitive is used in ALL five surfaces:
- **Top navbar** (`Layout.jsx`) — `size="sm"`, pulls `user.equipped`
- **HomePage welcome bar** — `size="md"`
- **Settings hero** — `size="xl"`
- **Leaderboard rows + podium** — every row from `/api/leaderboard` includes `equipped` per user; podium avatars are `size="lg"` with `animate-bounce` for rank 1
- **Shop live preview** — `size="xl"` above the catalogue, updates live as the user equips/unequips

`equipped` state hydrates on app boot from `GET /api/auth/me` and updates live via `onUserUpdate({ ...user, equipped: next })` after every equip toggle. Since `<Avatar>` is a controlled component reading from user state, all five surfaces update simultaneously.

---

## 13. Item Shop

### Catalogue
35 wearables across 4 rarity tiers, defined in `SHOP_CATALOGUE` in `quiz_backend.py`:

| Rarity | Cost | Count | Effort (@ 25 gems/quiz) |
|---|---|---|---|
| Common | 150-250 | 6 | 6-10 quizzes |
| Rare | 400-600 | 12 | 16-24 quizzes |
| Epic | 900-1200 | 10 | 36-48 quizzes |
| Legendary | 2200-3000 | 7 | 88-120 quizzes |

### Currency: 💎 Gems
Earned via quizzes:
- **+2** per correct answer
- **+5** per completed quiz
- **+50** on a rank-up

Stored in `users.gems` (BIGINT). Never decreases except on shop redemption.

### Purchase flow — `POST /api/shop/redeem`
1. Verify JWT, extract user_id
2. **7-day account-age gate**: `_account_age_days(user_id)` reads `users.created_at`. If age < `MIN_ACCOUNT_AGE_DAYS` (7), reject with `403 "Shop unlocks in N days"`
3. Look up item by `reward_id`, get its `cost`
4. In one transaction:
   - `SELECT gems FROM users WHERE id = %s FOR UPDATE` (row-level lock)
   - Check `gems >= cost`, else `400 "Need X gems, have Y"`
   - Check `user_rewards` for existing row → `400 "Already owned"`
   - `UPDATE users SET gems = gems - cost`
   - `INSERT INTO user_rewards (user_id, reward_id, cost)`
   - Commit
5. Return new gem balance + the item

The `UNIQUE (user_id, reward_id)` constraint is the ultimate defense against double-purchase — race conditions during the transaction can't slip through.

### Equip / Unequip — `POST /api/shop/equip`
- Body: `{reward_id: "hat_grad" | null, slot: "hat"}`
- Verifies the user owns the item (`SELECT 1 FROM user_rewards WHERE ...`)
- Updates `users.equipped` (JSON) — sets or clears the target slot
- Returns the new full `equipped` object

### Shop lock UX
- Backend `/api/shop` returns `shop_unlocked: bool`, `days_until_unlock: int`, `min_account_age_days: int`
- Frontend renders a big 🔒 lock card when `shop_unlocked === false` — users can browse the catalogue (build desire) but every Buy button is disabled
- Backend also defends: even if a user POSTs directly to `/api/shop/redeem`, the age-gate rejects with 403

### Rarities in the frontend
`ShopPage.jsx` has a `RARITY` map with:
- Sort rank (0/1/2/3) — items sort common→legendary within each slot
- Badge color classes — Common gray / Rare blue `#3B9EFF` / Epic purple `#A855F7` / Legendary gold `#F4B100`
- Glow ring shadow — Rare+ tiles have a subtle colored aura

---

## 14. Question Images (Drive → Backend proxy)

Sheet cells can contain `IMAGE:filename.png` references. The backend must convert these to real URLs the browser can load.

### `cache.file_map` (populated at startup)
`QuestionCache.load_file_map()` calls Drive's `files.list()` with `q="'{QUESTION_FOLDER_ID}' in parents"`, paginated via `nextPageToken` (fix from a previous session: the 1000-file default page size was capping large folders). For every file returned:
```python
file_map[name]              = file_id     # exact filename
file_map[name.lower()]      = file_id     # case-insensitive
file_map[name_no_ext]       = file_id     # filename without extension
file_map[name_no_ext.lower()] = file_id
```
So a Sheet reference `IMAGE:PHY-CHIJ2022-P1-Pure-033.png` looks up cleanly regardless of case/extension exactness.

### `resolve_file_id()` at question-load time
Turns filename references into real Drive IDs. If no match found, returns the input unchanged (with a debug log) — the assumption being that it might already be a Drive ID.

### `/api/image/{file_id}` proxy endpoint
1. Check `_IMAGE_CACHE` (in-memory dict, FIFO eviction at 1000 entries) — return cached bytes if present
2. **Resolve via file_map** (added recently): try input as-is, lowercased, with `.png/.jpg/.jpeg/.gif/.webp/.PNG/.JPG` extensions. If matched, swap to the actual Drive ID.
3. **Prefix-match fallback**: for cases where the DB has a stale prefix like `PHY-CHIJ2022-P1-Pure-033-` and Drive has `PHY-CHIJ2022-P1-Pure-033-Setup.png`, scan file_map for keys starting with the input.
4. Call `drive_service.files().get_media(fileId=resolved_id)`, stream bytes
5. Cache under both original key + resolved ID
6. Return `Response(content=bytes, media_type="image/png")`

### Frontend `QImage` component
Wraps `<img>` with a diagnostic failure placeholder. On `onError`, it re-fetches the URL with `fetch()` to capture the actual HTTP status + JSON detail, then shows the user a "Likely cause" hint (stale ID vs permissions vs auth expired).

---

## 15. Leaderboard

### Ranking basis
XP-based. Three time windows:

| Period | SQL source |
|---|---|
| **Daily** | `daily_challenges.xp WHERE challenge_date = today` |
| **Weekly** | `daily_challenges.xp WHERE challenge_date >= start_of_week` |
| **All-time** | `users.xp` |

### Query pattern (weekly)
```sql
SELECT u.id, u.name, u.avatar_url, u.equipped, COALESCE(SUM(dc.xp), 0) AS score
FROM users u
LEFT JOIN daily_challenges dc
  ON dc.user_id = u.id AND dc.challenge_date >= %s
WHERE u.name IS NOT NULL AND u.name <> ''
GROUP BY u.id
ORDER BY score DESC, u.id ASC
```
`equipped` comes back per row so the frontend renders every avatar with wearables.

### Refresh
- Frontend fetches `/api/leaderboard?period=weekly` on mount
- No caching layer — every call runs the SQL fresh
- A small "reload" icon in the header re-fetches on demand
- The three tabs (Daily / Weekly / All-time) each hit the endpoint with a different `period` param

### Podium + list rendering
- Top 3 → animated podium (rank 1 higher, `animate-bounce` on rank 1's avatar)
- Ranks 4+ → scrollable list with row-based avatars
- The current user gets `(You)` next to their name and is highlighted (checked via matching `user_id`)

---

## 16. Teacher Dashboard

### Role separation
- **DB-driven**: `users.is_teacher BOOLEAN DEFAULT FALSE` — set manually via SQL for teacher accounts (no self-serve teacher signup)
- The `is_teacher` claim is embedded in the JWT so the frontend can route teachers straight to their dashboard on login
- Endpoint `/api/teacher/overview` calls `require_teacher(authorization)` which verifies the JWT AND asserts `is_teacher == True` → 403 otherwise
- Frontend `TeacherDashboard.jsx` component; App.jsx routes to it when `user.is_teacher === true`

### What the dashboard displays
Response from `/api/teacher/overview`:
```json
{
  "week_at_a_glance": {
    "total_students":         50,
    "active_students":        32,
    "total_quizzes":          187,
    "class_avg_pct":          72.3,
    "avg_quizzes_per_active": 5.8,
    "pass_rate_pct":          68,
    "inactive_count":         18
  },
  "weakest_topics":     [...top-N topics with worst class avg...],
  "inactive_students":  [...students who haven't quizzed in 7 days...],
  "consistency":        [...per-student consistency rows...],
  "consistency_summary": {
    "avg_days_active":            4.2,
    "students_with_streak_3plus": 22,
    "total_students_listed":      50
  }
}
```

The endpoint runs several SQL queries against `quiz_attempts + streaks + daily_challenges` and aggregates them. It's a read-only overview — teachers can't edit student data through this UI.

### Student/teacher view switch
There's no toggle. Whether you see the student UI or the teacher UI is determined 100% by `is_teacher` in the JWT — a single account is EITHER a student OR a teacher. If a teacher wants to test the student experience, they'd need a separate student account.

---

## 17. SEAB Syllabus Locking

Both frontend (`QuizMaker.jsx`) and backend implicitly assume topics match the official Singapore syllabus. Two regex maps drive this:

```js
SEAB_6091_ORDER      // Pure Physics — 20 topics
SEAB_COMBINED_ORDER  // Combined Sci Physics — 16 topics (some combined, e.g. Force+Pressure)
```

Topic names from the backend that don't match any regex are FILTERED OUT of the picker entirely (not just sorted to the bottom). This means teachers editing the Sheet with non-syllabus topic names see them disappear from the student picker — a soft-enforcement of syllabus alignment.

The regexes are forgiving on wording ("Energy" vs "Work, Energy and Power", "DC Circuits" vs "D.C. Circuits") but strict on inclusion.

---

## 18. Frontend Theme System (Light + Dark)

Two themes live side by side, controlled by a body class:

- **Light theme (default)**: cream body `#FBF4EC` + white cards + brand orange `#FF6A1A` + cyan avatar rings — the current "HabitGo" brand kit
- **Dark theme**: cosmic violet gradient body + navy `#1a1a35` cards + sky-blue brand + lavender avatar rings — the pre-rebrand "QuizQuest" look preserved as an opt-in

### Implementation layers
1. **CSS variables** in `:root` for light values, overridden in `body.theme-dark` for dark
2. **Tailwind utility overrides** for classes that compile to literal hexes (e.g. `body.theme-dark .bg-quiz-orange { background-color: #fb923c !important; }`) — these are needed because Tailwind bakes color values at build time and won't respond to CSS var changes
3. **Themed CSS variables for inline styles**: `--weekstrip-grad`, `--logo-grad`, `--avatar-ring` — components read these instead of hardcoding hexes, so a theme flip changes them without any React re-render
4. **`body::before` pseudo-element** with `position: fixed; inset: 0` paints the backdrop gradient — glued to the viewport so scrolling never reveals an "edge"
5. **`body.theme-dark::after`** paints the starfield with a slow `@keyframes stars-drift` animation
6. **Pre-paint bootstrap** in `index.html`: reads `localStorage.theme` before React mounts, applies `html.theme-pre-dark` immediately, then `body.theme-dark` on DOMContentLoaded — zero FOUC on reload

### Persistence
Theme choice lives in `localStorage.theme`. The Settings page has a 2-button picker (Light / Dark) with a confirmation modal before applying, since it's a dramatic visual change.

---

## 19. Week 22 Optimisation

> **[TO FILL IN with the specific slow-things story. Common suspects in a codebase of this shape:]**

Some candidates based on what I can see in the code:

1. **Question loading**: reading Google Sheets synchronously on every quiz request would be catastrophic — the fix in the code is the in-memory `QuestionCache` that loads once and reuses.
2. **Image serving**: without the `_IMAGE_CACHE` (in-memory FIFO dict), every diagram would hit Drive every time. The cache brings that down to one Drive round-trip per unique image per process lifetime.
3. **Google Drive file_map pagination**: an earlier version capped at 1000 files because the code didn't loop `nextPageToken`. Once folders grew past that, some questions couldn't resolve their images. Fix: paginate until token is gone (visible in `load_file_map()`).
4. **Leaderboard subquery**: the current query LEFT JOINs `daily_challenges` with a `SUM(dc.xp)`. For hundreds of users this is fine; for thousands you'd add an index on `daily_challenges(user_id, challenge_date)` or precompute rollups.
5. **Frontend bundle size**: Vite code-splitting via `lazy()` imports means each route lazy-loads its component (`SettingsPage`, `TeacherDashboard`, `QuizHistory` are all lazy in `App.jsx`). This keeps the initial JS payload small.

**Before/after numbers**: I don't have these from your history. If you were tracking p95 latency or bundle sizes, that's the shape of the "before/after" section you'd fill in here.

---

## 20. Known Limitations + Improvement Ideas

- **No refresh token flow** — 24h JWT expiry silently breaks the UI for returning users. Add a `/api/auth/refresh` endpoint or shorten access token + long-lived refresh token pattern.
- **No hot-reload of Sheet edits** — teachers must ping you to restart the backend. Add a `/api/admin/reload-questions` endpoint (teacher-only) that calls `cache.load_questions(force=True)`.
- **No CI** — schema migrations run on every startup via `information_schema.COLUMNS` checks. This is safe but slow on cold starts. Consider moving to Alembic once schema grows.
- **No test suite** — the app has been iterated by direct testing in the UI. A pytest suite covering `/api/quiz`, `/api/quiz/submit`, `/api/streak`, and `/api/shop/redeem` would catch regressions from adding new features.
- **File-based auto-formatter fights** — this codebase has been through many sessions where an auto-formatter chopped file tails during saves. Long term, isolate the backend to a folder outside your editor's aggressive save handling, or configure Prettier/Black scoping.

---

## 21. Repo Layout

```
quiz-maker-frontend/
├── src/
│   ├── App.jsx                    ← top-level router (case-based)
│   ├── index.css                  ← :root vars, body::before, theme overrides
│   ├── motion/index.js            ← Framer Motion variants (ease, correctPop, etc.)
│   ├── lib/cn.js                  ← classNames helper
│   └── components/
│       ├── Layout.jsx             ← top bar + bottom nav + avatar dropdown
│       ├── HomePage.jsx           ← daily card + streak/rank + weekly strip
│       ├── QuizMaker.jsx          ← quiz build form + quiz-taking + results
│       ├── PracticePage.jsx       ← saved quizzes list
│       ├── ShopPage.jsx           ← wearables catalogue with rarity
│       ├── LeaderboardPage.jsx    ← podium + list with tabs
│       ├── Settings.jsx           ← profile page (hero, stats, achievements)
│       ├── SettingsPage.jsx       ← app-wide preferences page (theme, edit, logout)
│       ├── TeacherDashboard.jsx   ← teacher-only view
│       ├── EditProfileModal.jsx   ← name + avatar editor
│       └── ui/
│           ├── Avatar.jsx         ← Mr Potato Head primitive + wearables registry
│           ├── Button3d.jsx       ← the signature "3D push" button
│           ├── Card.jsx           ← glass + solid variants
│           ├── Modal.jsx          ← animated confirm dialog
│           ├── Screen.jsx         ← page wrapper
│           ├── WeekStrip.jsx      ← Mon→Sun status chips
│           ├── ProgressBar.jsx    ← shimmer XP bar
│           ├── Motion.jsx         ← Stagger / StaggerItem wrappers
│           └── … (16 total)
├── index.html                     ← pre-paint theme bootstrap script
├── tailwind.config.js             ← quiz-* palette + Baloo 2 font
└── vite.config.js

quizMaker/
├── quiz_backend.py                ← the entire backend
├── credentials.json               ← Google service-account key (gitignored)
├── diagnose_images.py             ← utility to scan Sheet for broken image refs
└── requirements.txt

Shared:
├── MySQL 8                        ← managed instance
├── Google Sheets                  ← question bank + P6 math workbook
└── Google Drive folder            ← diagram images
```

---

## Appendix A — API Endpoint Reference

Grouped by concern. All require `Authorization: Bearer <JWT>` unless noted.

**Auth (no auth required)**
- `POST /api/auth/signup` → `{token, user}`
- `POST /api/auth/login` → `{token, user}`
- `POST /api/auth/google` → `{token, user}`
- `GET  /api/auth/me` → `{user}` (includes equipped wearables)
- `PUT  /api/auth/profile` → update `{name, avatar_url}`

**Quiz**
- `GET  /api/subtopics?level=pure|combined` → list of topics
- `GET  /api/difficulties` → `["Easy", "Medium", "Hard"]`
- `GET  /api/availability?level=pure|combined` → `{topic: {easy, medium, hard}}` counts
- `POST /api/quiz` → generates a quiz from filters
- `POST /api/quiz/submit` → scores + awards XP/gems, returns `{xp_delta, gems_delta, rank_up, ...}`

**Streak / Daily**
- `GET  /api/streak` → current + longest + freeze state
- `GET  /api/streak/week` → last 7 days for the weekly strip
- `GET  /api/daily-challenge?subject=Physics` → today's progress

**History**
- `GET  /api/history` → list of attempts
- `GET  /api/history/{attempt_id}/quiz` → the frozen questions for retake

**Rank / Stats**
- `GET  /api/ranks` → all tier metadata
- `GET  /api/stats` → overall + first-attempt accuracy + per-topic breakdown

**Shop**
- `GET  /api/shop` → catalogue + owned + equipped + `shop_unlocked` gate
- `POST /api/shop/redeem` → buy an item
- `POST /api/shop/equip` → equip/unequip an owned item

**Social**
- `GET  /api/leaderboard?period=daily|weekly|alltime` → ranked users with equipped

**Images**
- `GET  /api/image/{file_id}` → PNG bytes (proxied from Drive, cached)

**Teacher (requires `is_teacher`)**
- `GET  /api/teacher/overview` → weekly class dashboard

---

*Doc generated for CuriousLab HabitGo. Sections marked "[TO FILL IN]" need
your operational/historical knowledge to complete. Everything else is
derived from the current source of truth in `quiz_backend.py` and
`quiz-maker-frontend/src/`.*
