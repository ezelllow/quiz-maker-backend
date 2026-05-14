# Phase 0 — Ranking & Streak System Spec

This locks the numbers and rules for the HabitGo ranking + streak system so the
±2 trap can't creep back in. Status tags: **[BUILT]** = shipped in Phase 1,
**[SPEC]** = decided here, not yet built.

---

## 1. Subject taxonomy

- A **`Subject`** column in the Google Sheet. If absent, every question defaults to `Physics`. **[BUILT]**
- A **`Topic`** column groups questions within a subject (falls back to the legacy `Subtopic` column). **[BUILT]**
- **Rank is per-subject.** A student is "B3 Physics", not "B3" overall.
- Today: Physics only. Math activates automatically once the `Subject` column has `Math` rows.

## 2. Score → grade band

Percentage maps to a Singapore O-Level-style band. A1 best, F9 lowest. **[BUILT]**

| Band | Min % | | Band | Min % |
|------|-------|---|------|-------|
| A1 | 75 | | C6 | 50 |
| A2 | 70 | | D7 | 45 |
| B3 | 65 | | E8 | 40 |
| B4 | 60 | | F9 | 0 |
| C5 | 55 | | | |

> **Open decision for Lloyd:** these cut-points are an approximation. As an MOE
> specialist, confirm or replace with the boundaries you want. They live in one
> `score_to_band()` function — trivial to tune.

## 3. Placement quiz **[BUILT]**

- **15 questions**, one subject.
- Stratified across difficulty (round-robin over difficulty buckets) and shuffled so topics vary.
- Taken once — on signup, or first login if the user has no rank yet.
- Sets the **starting rank only**. No streak, no rank movement from placement.
- Result stored in `user_subject_ranks`.

## 4. Rank movement rule **[SPEC — Phase 2]**

Rank moves on a rolling window of the **last 5 ranked quizzes** (Daily Challenges)
for that subject. Placement and Practice quizzes never affect rank.

- **Promote** one band when **3 of the last 5** ranked quizzes score at or above the *next* band's cut-point.
- **Demote** one band only when **4 of the last 5** fall below the *current* band's floor.
- Asymmetric on purpose — easier to hold a band than to drop it, so one bad day doesn't tank a student.
- One band per update. No skipping bands up or down.

## 5. Streak qualification rule **[SPEC — Phase 2]**

The anti-gaming lock is **who picks the questions**, not the score.

- A day's streak is earned by completing that day's **Daily Challenge** and scoring **≥ 60%** (the floor).
- **Daily Challenge** = system-generated, **10 questions**, drawn from the user's
  weak topics in their subject, difficulty centred on their current rank band.
  The student does **not** pick topic, difficulty, or questions.
- Failed the floor? **Retry the same day** with a fresh question set. Failing an
  attempt doesn't break the streak — only failing to clear it before the day ends does.
- One Daily Challenge counts per calendar day.
- **Streak freeze:** 1 per 7 days, auto-applied. A missed day consumes a freeze
  instead of resetting. No freeze available → streak resets to 0.

> Streak = habit (easy floor, ~85–90% achievable). Rank = mastery (section 4).
> They are deliberately decoupled.

## 6. Practice vs Daily Challenge boundary **[SPEC — Phase 2]**

| | Practice Mode | Daily Challenge (Ranked) |
|---|---|---|
| Who picks questions | Student — topic, difficulty, count | System — weak-topic weighted, rank-level difficulty |
| Length | Student's choice | 10 |
| Counts toward streak | No | Yes |
| Counts toward rank | No | Yes |
| Frequency | Unlimited | Once per day (same-day retries allowed) |

The existing quiz maker = Practice Mode. The Daily Challenge is a new, separate flow.

## 7. Data model

- **`user_subject_ranks`** — `user_id, subject, rank_band, rank_score, placed_at, updated_at`, `UNIQUE(user_id, subject)`. **[BUILT]**
- **`quiz_attempts`** — needs a new **`quiz_type`** column (`practice` | `placement` | `ranked` | `daily`) so rank logic can filter which attempts count. **[SPEC — Phase 2]**
- **`streaks`** — `user_id, current_streak, longest_streak, last_qualified_date, freezes_available, freeze_last_granted`. **[SPEC — Phase 2]**
- **`rank_history`** — `user_id, subject, rank_band, recorded_at`. Feeds the trend line and the parent report. **[SPEC — Phase 3]**

## 8. Endpoints

- **[BUILT]** `GET /api/subjects`, `GET /api/placement/questions`, `POST /api/placement/submit`, `GET /api/ranks`
- **[SPEC — Phase 2]** `GET /api/daily-challenge` (today's set), `POST /api/daily-challenge/submit`, `GET /api/streak`

## 9. Build status

| Phase | Scope | Status |
|---|---|---|
| 1 | Subject support, placement quiz, F9–A1 banding, starting rank, rank display | **BUILT** |
| 1b | Rolling-average rank updates from ranked quizzes; parent-report data pipe | SPEC |
| 2 | Daily Challenge, streak, streak freeze, `quiz_type` column, `streaks` table | SPEC |
| 3 | Rank-up animation, A1 end-game challenge, `rank_history` + trend | SPEC |

## 10. Open decisions for Lloyd

1. **Grade cut-points** (section 2) — confirm or replace.
2. **Streak floor** — locked at 60%. Confirm.
3. **Rolling window** — locked at last 5 ranked quizzes. Confirm.
4. **Promote / demote thresholds** — 3-of-5 up, 4-of-5 down. Confirm.
5. **Existing users** — Phase 1 currently shows them the placement quiz once on next
   login. Keep, or grandfather them in with a default/unranked state?
6. **HabitGo active users today** — still unanswered. This number decides whether
   Phase 2 is built now or parked until there are students to retain.
