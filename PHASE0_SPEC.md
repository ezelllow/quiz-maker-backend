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

Each band carries a student-facing **tier name** (cosmetic layer over the band). *(Locked 2026-05-14.)* **[BUILT]**

| Band | Tier | | Band | Tier |
|------|------|---|------|------|
| F9 | Beginner | | C5 | Expert |
| E8 | Apprentice | | B4 | Elite |
| D7 | Advanced | | B3 | Master |
| C6 | Scholar | | A2 | Champion |
| | | | A1 | Legend |

> We kept the **real 9 O-Level bands** rather than expanding to a 12-tier ladder —
> staying faithful to SEAB grading (no invented F8/F7/E7 grades a parent would
> question). Bottom-end granularity, if wanted later, is roadmap item T4.3.

## 3. Placement quiz **[BUILT]**

- **15 questions**, one subject.
- Stratified across difficulty (round-robin over difficulty buckets) and shuffled so topics vary.
- Taken once — on signup, or first login if the user has no rank yet.
- Sets the **starting rank only**. No streak, no rank movement from placement.
- Result stored in `user_subject_ranks`.

## 4. Rank movement rule **[BUILT — re-wired 2026-05-14 afternoon]**

**The Daily Challenge moves rank.** Rank and streak run on the *same input* (Daily
Challenge submissions), with different mechanics.

Rank moves on a rolling window of the **last 5 Daily Challenges** for that subject,
counted since the last rank change (placement or a previous movement reset the window):

- **Promote** one band when **3 of the last 5** Daily Challenges score at or above
  the *next* band's % floor.
- **Demote** one band only when **4 of the last 5** fall below the *current* band's floor.
- Asymmetric on purpose — easier to hold a band than to drop it, so one bad day
  doesn't tank a student.
- One band per move. No skipping bands up or down.
- After any movement, the window resets — fresh 5 needed before the next change.

> **Design history.** The original Phase 2 build had this. It was unwired earlier on
> 2026-05-14 (morning) over a "Daily Challenge does two jobs" concern, with a future
> periodic Rank Assessment spec'd as the would-be replacement. Re-wired the same
> day (afternoon) on Lloyd's decision — knowingly re-accepting the trade-off that
> one bad Daily Challenge affects both streak and rank.

> **Consequence for T5.1.** The periodic Rank Assessment (`PHASE0_SPEC.md` §4a stays
> for reference, ROADMAP §T5.1) is **no longer required for rank to move** — rank
> moves on the Daily Challenge again. T5.1 becomes optional / lower-priority: build
> only if a separate, deeper assessment is genuinely wanted later.

> **Consequence for T5.3 (rank-up animation).** The trigger now exists — rank-up
> events fire on Daily Challenge submissions. The animation can be built whenever
> Lloyd wants; it no longer waits on T5.1. The dormant `daily-rank-change` banner
> in `DailyChallenge.jsx` becomes live again automatically (no frontend change
> needed — `rank_change` will start returning real `{changed: true, ...}` payloads).

### 4a. Rank-up & badge animation system **[SPEC — ships with T5.1]**

The premium rank progression experience. Spec'd from Lloyd's full vision on
2026-05-14. **Not built** — there is no rank-change event until the Rank Assessment
(T5.1) exists, and the whole system is build-gated behind deployment + a real
cohort. This section captures the vision faithfully so nothing is lost; the
build happens *with* T5.1, never before.

**Stack reality (what's viable vs. not).** The original brief assumed Figma +
Next.js + Tailwind + TypeScript. The actual app is React + **Vite**, plain `.jsx`,
plain CSS. So:
- ✅ **Framer Motion** — idle/hover/UI motion. Works in Vite.
- ✅ **GSAP** — cinematic rank-up/-down timelines. Works in Vite.
- ✅ **tsParticles** — particle bursts + ambient particles. Works in Vite.
- ⚠️ **React Three Fiber** — works in Vite, but *defer hard*. Legendary-rank /
  special-cinematic use only, if ever. Do not 3D-ify the app.
- ❌ **Figma / Illustrator** — no tooling available. SVG badges are hand-authored
  or produced via `RANK_BADGE_BRIEF.md`.
- ❌ **Next.js / Tailwind / TypeScript** — not the project's stack. Build in
  `.jsx` + plain CSS. File names below are `.jsx` / `.js`, not `.tsx` / `.ts`.
  No framework migration — that is explicitly out of scope.

**Badge design system.** SVG only (no PNG) — scalable, animatable, performant.
Each badge is layered: outer frame, inner emblem, glow layer, highlight/reflection
layer, shadow layer. One reusable component system so all 9 share a cohesive look.
Progression: higher tiers add stronger glow, more emblem complexity, animated
shine, floating particles, a premium metallic/crystal feel.

**Badge idle animations** *(the one slice that needs no rank-up trigger — badges
already render in 5 places, so this could be built earlier than the rest).* Subtle
idle float, slow glow pulse, periodic shine sweep, hover elevation. Framer Motion
for idle/hover; CSS gradients for the shine sweep; SVG masks for reflections.

**Rank-up cinematic — GSAP timeline.** One orchestrated timeline, not independent
animations:
1. XP/score bar fills *(see open question — HabitGo has no XP system)*
2. Screen glow intensifies
3. Background darkens slightly
4. Subtle UI shake
5. Particle burst (tsParticles)
6. Old badge scales down / fades
7. New badge rotates + scales in (overshoot)
8. Glow pulse expands outward
9. "RANK UP" text animates in
10. Continue button fades in

**Rank-down.** Also a GSAP timeline, but darker/desaturated, softer particles,
controlled downward motion, fading glow. Acknowledgement, not punishment — never
dramatic or frustrating.

**Motion language.** Use: blur transitions, glow pulses, additive-lighting feel,
scaling overshoot, eased curves (spring/`power` easing), staggered particles,
smooth opacity fades. Avoid: linear timing, harsh movement, cheesy game effects,
over-neon visuals.

**File structure** (adapted to the real stack):
- `src/components/rank/` — `RankBadge.jsx`, `RankUpAnimation.jsx`,
  `RankDownAnimation.jsx`, `ParticleLayer.jsx` (`XPBar.jsx` pending the XP decision)
- `src/assets/ranks/` — `beginner.svg` … `legend.svg`
- `src/data/rankMetadata.js`

**Metadata-driven (the smart part).** `rankMetadata.js` defines per rank: colors,
glow intensity, particle type, animation tier, icon asset, rarity level, unlock
effects. The animation components *read* this metadata — no per-rank hardcoded
animation logic. Adding/retuning a rank is a data edit, not a code edit.

**Trigger contract.** The rank-up/-down components mount from the Rank Assessment
result screen when its submit response has `rank.changed === true`, reusing the
existing `{ changed, direction, old_band, new_band }` shape (+ tier name/icon) —
the same shape the now-dormant `daily-rank-change` banner in `DailyChallenge.jsx`
already reads. Components stay pure-presentational: props in, animation out.

**Open questions for build time:**
1. **XP bar.** HabitGo has *no XP system* — it has streaks and ranks. Step 1 of the
   rank-up timeline ("XP bar fills") maps to nothing that exists. Options: drop the
   step; replace it with the Rank Assessment *score* bar filling; or introduce XP as
   genuine new scope (flag: that's its own project, not part of this).
2. **R3F.** Confirm whether legendary-rank 3D is in scope at all, or cut entirely.
3. **Particle burst.** `tsParticles` vs. a lighter hand-rolled burst — decide once
   we see real bundle-size impact.

**Cleanup that comes with this.** When T5.1 ships, remove the dormant
`daily-rank-change` banner from `DailyChallenge.jsx` — rank no longer moves there.

## 5. Streak qualification rule **[BUILT — rewritten 2026-05-17]**

**The streak rewards *effort over the whole day*, not one perfect quiz.**

A student earns today's streak when they accumulate **`DAILY_CORRECT_TARGET = 10`
correct answers across any number of Practice quizzes** in the day. Correct answers
**stack** across attempts — they can build it up in chunks of 5, or 3+3+4, or one
big 10. With enough effort, anyone gets the streak.

Practice IS the daily mechanism — there is no separate system-picked daily quiz.
The student picks topic / difficulty / question count themselves and takes as many
practice quizzes as they want; the backend tallies their daily correct count.

Mechanics:

- Every `POST /api/quiz/submit` calls `_credit_daily_practice(...)` which upserts
  today's row in `daily_challenges` with cumulative `score` (correct) and `total`
  (attempted). When `score >= 10` the row is marked `passed = TRUE`.
- The moment `passed` flips from FALSE → TRUE that day, `_award_streak_day(...)`
  fires — increments `current_streak`, updates `longest_streak`, sets
  `last_qualified_date = today`. Idempotent: repeat hits the same day are no-ops.
- **Streak freeze:** 1 per 7 days, auto-applied. A missed day consumes a freeze
  instead of resetting. No freeze available → streak resets to 0.
- One streak credit per calendar day (Singapore time). Bonus practice after hitting
  the target is encouraged but doesn't compound the streak that day.

> The old per-quiz percentage floor (rank-relative, 35%-65%) is **obsolete** under
> this model. The whole concept of "pass-rate threshold on one daily quiz" is
> gone — instead it's "how many correct over the day." `STREAK_FLOOR` and
> `streak_floor_for_rank()` stay in the code as no-ops in case the old system-picked
> daily flow is ever resurrected.

> The legacy `/api/daily-challenge/submit` endpoint and `DailyChallenge.jsx`
> component are not deleted (they still work in isolation), but no part of the
> live UI calls them — they are effectively dead code under the new model.


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
2. **Streak floor** — ~~locked at 60%~~ **RESOLVED 2026-05-14:** rank-relative — floor of the band two ranks below the student's rank, clamped at F9 = 35%.
3. **Rolling window** — locked at last 5 ranked quizzes. Confirm.
4. **Promote / demote thresholds** — 3-of-5 up, 4-of-5 down. Confirm.
5. **Existing users** — Phase 1 currently shows them the placement quiz once on next
   login. Keep, or grandfather them in with a default/unranked state?
6. **HabitGo active users today** — still unanswered. This number decides whether
   Phase 2 is built now or parked until there are students to retain.
