# HabitGo — Implementation Roadmap

Ordered by urgency. Companion to `PHASE0_SPEC.md` (which locks the numbers).
Tags: **[BUILT]**, **[TODO]**.

---

## What's already built

**Core app**
- Quiz maker — topic + difficulty filters, up to 3 topics, TEXT / IMAGE / TABLE question types
- Auth — email + Google OAuth, JWT
- Saved Quizzes, History, Review screen, Retake flow
- Dashboard — accuracy, performance trend, per-topic + per-difficulty breakdown, weakest topics
- Profile editing — avatar upload + display name
- Dark glassmorphic UI redesign
- Deployed — frontend on Cloudflare Pages, backend on Render, MySQL on Railway

**Phase 0** — `PHASE0_SPEC.md` written (locks streak/rank rules + numbers)

**Phase 1**
- Subject support (`Subject` sheet column, defaults to Physics; Math placeholder in UI)
- "Subtopic" → "Topic" rename, Subject picker
- Placement quiz — 15 Q stratified across difficulty/topics
- `user_subject_ranks` table, F9–A1 banding, `score_to_band()`
- Endpoints: `/api/subjects`, `/api/placement/questions`, `/api/placement/submit`, `/api/ranks`
- Placement gate wired into `App.jsx` (post-signup), rank display on Dashboard

---

## TIER 0 — Fix & ship (this week — nothing else is trustworthy until this is done)

**T0.1 — Deploy Phase 1.** It's built and tested locally but **not pushed**. Until it's deployed it doesn't exist for users. `git push` both repos, verify on production, confirm the `user_subject_ranks` migration ran on Render. **[TODO]**

**T0.2 — Fix the TEXT-question scoring bug.** In `QuizMaker.jsx` submit, `userAnswer` stores the full option line (`"C. lamp X..."`) but `question.answer` is just the letter (`"C"`) — the `===` comparison fails, so **TEXT questions score 0**. This corrupts quiz scores, History, Dashboard stats, and *will* corrupt rank once Phase 2 ties rank to scores. Fix: normalise both sides to the answer letter before comparing (the `answerKey()` helper in `Placement.jsx` already does this — reuse it). **[TODO]**

---

## TIER 1 — Make rank real (Phase 1b — high, revenue-aligned)

**T1.1 — Add `quiz_type` column to `quiz_attempts`** (`practice` | `placement` | `ranked` | `daily`). Tiny migration, but it unblocks everything below — rank logic and the stats split both need it. **[TODO]**

**T1.2 — Parent-report data pipe.** Make rank + rank history cleanly queryable so the parent consultation can show "B4 Physics, trending up." The polished report document is a separate build; this tier just guarantees the *data* exists. This is the revenue link — prioritise it over the streak engine. **[TODO]**

> Note: rank *movement* (the rolling-average promote/demote logic) is coupled to the
> Daily Challenge, so it lives in Tier 2, not here.

---

## TIER 2 — The gamification engine (Phase 2 — GATED, see below)

**T2.1 — `streaks` + `rank_history` tables.** **[TODO]**

**T2.2 — Daily Challenge.** System-generated 10-question quiz, weak-topic weighted, difficulty centred on the user's rank. Endpoints: `GET /api/daily-challenge`, `POST /api/daily-challenge/submit`. **[TODO]**

**T2.3 — Streak logic.** ≥60% floor to qualify, same-day retries allowed, 1 freeze per 7 days. Endpoint: `GET /api/streak`. **[TODO]**

**T2.4 — Rank movement.** Wire promote (3-of-5) / demote (4-of-5) / fresh-window rule to Daily Challenge submissions. Only Daily Challenges move rank. **[TODO]**

**T2.5 — Daily Challenge UI + streak display + rank-up moment.** **[TODO]**

---

## TIER 3 — Practice growth view + legibility

**T3.1 — `quiz_type` filter on `/api/stats`** so the Practice growth view isn't polluted by Daily Challenge scores. **[TODO]**

**T3.2 — Practice growth view.** Per-topic accuracy trend, "topics improved" count, week-on-week accuracy delta. Lead with *improvement*, not volume. Fold into the existing Dashboard tab. **[TODO]**

**T3.3 — Legibility copy.** UI text connecting Practice → Daily Challenge ("Drill your weak topics to ace tomorrow's Daily Challenge"). Near-zero effort, makes Practice's incentive visible. **[TODO]**

---

## TIER 4 — Polish (only if the data says it's needed)

**T4.1 — Math auto-enable.** When the `Subject` sheet column has Math rows, flip the Subject picker's Math option from disabled → active (drive it off `/api/subjects` instead of the hardcoded `disabled`). **[TODO]**

**T4.2 — A1 end-game challenge** so top students don't plateau. **[TODO]**

**T4.3 — Rank decay / prestige tiers** — only if retention data shows a need. **[TODO]**

---

## Decisions Lloyd still owes (from `PHASE0_SPEC.md` §10)

1. Grade cut-points — confirm or replace.
2. Streak floor (60%) and promote/demote thresholds (3-of-5 / 4-of-5) — confirm.
3. Existing users — keep the "placement quiz once on next login" gate, or grandfather them in?
4. **HabitGo active-user count today** — see the gate below.

---

## The build gate (read this before starting Tier 2)

- **Tier 0 + Tier 1** = finishing what's started and making the data trustworthy. Revenue-aligned (the rank + parent data feeds consultations). **Proceed.**
- **Tier 2 is GATED.** It's a retention engine. Do not start it until the HabitGo
  active-user count is known. Building a retention engine with no one to retain is
  the build-instead-of-sell trap.
  - Small/zero active users → ship Tier 0 + 1, then stop building and run diagnostics + enrolment per the strategic plan.
  - Real cohort → Tier 2 is justified; build it.
- **Tier 3 / 4** — after Tier 2, or never. Not urgent.
