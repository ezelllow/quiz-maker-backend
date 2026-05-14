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

> Built on 2026-05-13 after Lloyd's explicit override of the gate below.

**T2.1 — `streaks`, `rank_history`, `daily_challenges` tables.** **[BUILT]**

**T2.2 — Daily Challenge.** System-generated 10-question quiz, weak-topic weighted. Endpoints: `GET /api/daily-challenge`, `POST /api/daily-challenge/submit`. **[BUILT]** *(difficulty-centring on rank deferred — v1 does weak-topic weighting + mixed difficulty)*

**T2.3 — Streak logic.** Same-day retries, 1 freeze per 7 days, lazy expiry on read. Endpoint: `GET /api/streak`. **[BUILT]** *(2026-05-14: flat 60% floor replaced with a rank-relative floor — band two ranks below the student's, F9 = 35%.)*

**T2.4 — Rank movement.** ~~Promote (3-of-5) / demote (4-of-5) wired to Daily Challenge submissions.~~ **[REVERTED 2026-05-14]** — unwired because it made the Daily Challenge drive both streak and rank. Rank now holds at the placement band; movement is deferred to a separate periodic Rank Assessment (see T5.1 and `PHASE0_SPEC.md` §4). The temporary `/api/test/daily-rank` endpoint + `TestPanel.jsx` were removed with it.

**T2.5 — Daily Challenge UI + streak display + rank-up moment.** `DailyChallenge.jsx` page, "🔥 Daily" nav item, streak card on the Dashboard, rank-change banner in the result screen. **[BUILT]** *(rank-change banner is dormant after T2.4 was reverted — it degrades gracefully since `rank.changed` is always false.)*

---

## TIER 3 — Practice growth view + legibility

> Built on 2026-05-14 after Lloyd's second explicit override of the build gate.

**T3.1 — `quiz_type` filter on `/api/stats`** so the Practice growth view isn't polluted by Daily Challenge scores. `WHERE quiz_type = 'practice'` added to the stats query. **[BUILT]**

**T3.2 — Practice growth view.** Per-topic accuracy trend (earlier vs recent half), "topics improved" count, week-on-week accuracy delta. `growth` object added to `/api/stats`; `GrowthPanel` folded into the Dashboard, leads with improvement. **[BUILT]**

**T3.3 — Legibility copy.** Practice→Daily hint in the Growth panel; Daily→Practice hint on the Daily Challenge intro screen. **[BUILT]**

---

## TIER 5 — Rank rework (separate rank from the Daily Challenge)

> Spec'd 2026-05-14. Build-gated — do not build until HabitGo is deployed with a real cohort.

**T5.1 — Periodic Rank Assessment.** A separate system-generated assessment that reuses the placement infrastructure (stratified draw, `score_to_band()`). Runs on its own cadence (monthly, or on-demand with a cooldown — TBD). The *only* thing that moves rank. Daily Challenge → streak only; Practice → neither. Spec in `PHASE0_SPEC.md` §4. **[SPEC — not built]**

**T5.2 — Rank tier names + icons.** Student-facing tier name + emoji icon per band (🌱 Beginner → 👑 Legend). The O-Level letter codes (A1–F9) are kept as the internal data model but **never shown to students** — every rank display is icon + tier name. Appears in the navbar badge, Settings, Dashboard, Daily Challenge intro, and the placement result. **[BUILT 2026-05-14]**

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
