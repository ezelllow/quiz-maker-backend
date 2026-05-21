# HabitGo — Edge Case Test Checklist

Grounded in the current `quiz_backend.py` logic (streak, rank/XP, daily).
Tick each box once verified. ⚠️ marks spots where the code looks inconsistent — check these carefully.

Key facts to keep in mind while testing:
- "Today" is **Singapore time** (`_sg_today()`, UTC+8) — not the user's device clock.
- XP, gems, daily-goal credit and streak are awarded **only by daily quizzes** (`quiz_type='daily'`). Practice grants nothing.
- Streak is touched in two places: `_award_streak_day` (on submit when the goal is crossed) and `_lazy_streak_maintenance` (on every `/api/streak` and `/api/streak/week` read).

---

## A. Streak function

### A1. First-time / empty state
- [ ] Brand-new user, no quiz yet → `/api/streak` shows `current_streak: 0`, `longest_streak: 0`, `freezes_available: 1`.
- [ ] First streak row is created with 1 freeze pre-granted — confirm the new user really has 1, not 0.
- [ ] `did_today` is `false` before the user passes the daily goal.

### A2. Earning a day
- [ ] Hit the daily goal (e.g. 10 correct) in a single daily quiz → streak goes 0 → 1.
- [ ] Hit the goal **across multiple daily quizzes** the same day (e.g. 6 correct, then 4) → streak increments **once**, on the attempt that crosses the goal.
- [ ] Already passed today, submit another daily quiz → streak does **not** increment again (idempotent).
- [ ] Submit a daily quiz with 0 correct → no streak change.
- [ ] Submit below the goal (9/10) → no streak; submit 1 more correct → crosses goal, streak +1.

### A3. Consecutive days
- [ ] Pass Day 1, pass Day 2 → streak 1 → 2.
- [ ] Pass 7 days in a row → streak = 7, and the **+50 XP streak-milestone** fires.
- [ ] Pass 14 days → milestone fires again at 14 (every 7).
- [ ] `longest_streak` keeps up with `current_streak` as it climbs.

### A4. Missed days + freeze
- [ ] Miss exactly 1 day, have ≥1 freeze → next time the app reads `/api/streak`, freeze auto-fires, streak preserved, the missed day later shows ❄️ on the week strip.
- [ ] Miss exactly 1 day, have 0 freezes → streak resets to 0 on read.
- [ ] Miss 2 days, have only 1 freeze → streak resets.
- [ ] Miss 3+ days → streak resets regardless of freezes (cap is 2, can't bridge 3).
- [ ] ⚠️ Miss 2 days with 2 freezes → streak **survives** (both freezes consumed), but `freeze_used_date` only records **one** day (`today − 1`). Confirm the week strip is acceptable showing only 1 ❄️ for a 2-day bridge.
- [ ] User stays away several days then opens the app → gap is measured from `last_qualified_date`; freeze fires (or streak dies) correctly on that first read.
- [ ] After a freeze auto-fires, re-read `/api/streak` immediately → it does **not** fire again / double-consume (last_qualified advanced to the bridged day).

### A5. Freeze grant & cap
- [ ] New ISO week (Mon–Sun) starts → +1 freeze granted on the first read/award that week.
- [ ] Already at 2 freezes, new week → stays at 2 (no overflow past `GEMS_FREEZE_CAP`).
- [ ] Multiple reads in the same week → freeze granted only **once** that week, not per-read.
- [ ] Buy a freeze from the shop at 1 → goes to 2.
- [ ] Buy a freeze when already at 2 → blocked / rejected (cap).
- [ ] Week rollover test: pass on Sunday, then Monday → freeze regen triggers on the Monday.

### A6. Timezone / midnight boundary
- [ ] Quiz completed at 11:59 pm SG vs 12:01 am SG → counted to different streak days.
- [ ] User on a non-SG device → day boundary still follows SG midnight, not local midnight.

### A7. Week strip (`/api/streak/week`)
- [ ] Passed earlier day → `completed`.
- [ ] Missed earlier day (no freeze) → `missed`.
- [ ] Day bridged by a freeze → `freeze_used` (❄️).
- [ ] Today, goal not yet hit → `today`.
- [ ] Future day this week → `upcoming`.
- [ ] Strip and the `/api/streak` counter agree with each other after a freeze fires.

### A8. Test panel parity
- [ ] Test 🧊 button simulates a genuinely **missed** day (not skip-and-complete).
- [ ] Streak test buttons (next day / freeze) produce the same result as real play.
- [ ] Streak reset test endpoint returns the user to a clean 0-state.

---

## B. Rank / XP function

### B1. XP earning (daily only)
- [ ] Practice quiz → **0 XP, 0 gems**, no daily credit, no streak. Verify nothing moves.
- [ ] Daily quiz, 5 correct, **Easy** → base = 5 × 10 × 1.0 = **50 XP**.
- [ ] Daily quiz, 5 correct, **Medium** → 5 × 10 × 1.25 = 62.5 → rounds to **63 XP**.
- [ ] Daily quiz, 5 correct, **Hard** → 5 × 10 × 1.5 = **75 XP**.
- [ ] Perfect score on ≥3 questions → **+20** perfect bonus.
- [ ] Perfect score on a 2-question quiz → **no** perfect bonus (minimum is 3).
- [ ] Quiz that crosses the daily goal → **+15** daily-goal bonus, once.
- [ ] Already passed today, another daily quiz → **no** +15 again.
- [ ] Difficulty multiplier applies to the **base only** — bonuses (+20/+15/+50) are not multiplied.

### B2. Level
- [ ] 0 XP → Level 1; 49 XP → Level 1; 50 XP → Level 2 (`floor(xp/50)+1`).
- [ ] Level badge updates immediately after a daily quiz.

### B3. Rank tiers
- [ ] Thresholds: 0 Cadet · 200 Pilot · 500 Navigator · 1200 Commander · 2500 Captain · 5000 Star Admiral.
- [ ] 199 XP → still Cadet; exactly 200 → Pilot (boundary inclusive).
- [ ] Star Admiral is the cap — XP past 5000 stays Star Admiral, `xp_next` is null, no "next rank" UI breakage.
- [ ] A daily quiz that crosses a threshold → rank-up banner shows **and +50 gems** awarded.
- [ ] A daily quiz that doesn't cross a threshold → no rank-up, no +50.
- [ ] One quiz that jumps far enough to cross **two** thresholds → handled cleanly (rank shown is the final one; only one rank-up event).

### B4. Leaderboard
- [ ] All-time tab ranks by `users.xp`; Daily/Weekly tabs rank by correct-answer counts.
- [ ] Leaderboard refreshes after a daily quiz changes your XP.
- [ ] Practice quiz → leaderboard position unchanged.
- [ ] "You" / `is_me` highlight is correct, including after a profile rename.
- [ ] Two users with equal XP → tie ordering is stable, no crash.
- [ ] A user with 0 XP appears correctly (bottom of all-time).

### B5. XP edge / test tools
- [ ] Test XP-grant → level and rank recompute correctly.
- [ ] Test gems-grant → balance updates, navbar pill updates.
- [ ] Progression reset → back to Cadet / Level 1 / 0 XP / rank pill correct.
- [ ] XP can never go negative (clamped at 0).

---

## C. Daily function

### C1. Availability & lock
- [ ] `/api/daily-challenge` returns 10 questions.
- [ ] Before completing the goal → `already_passed_today: false`, daily playable.
- [ ] After hitting the goal → `already_passed_today: true`, daily **locks** (lock screen shows).
- [ ] Once locked, only Practice is available — and Practice still grants nothing.
- [ ] `attempts_today` increments on each submit.

### C2. Daily goal (10 / 15 / 20)
- [ ] Default goal is 10; user can set 15 or 20 in Profile.
- [ ] With goal 15: pass requires 15 cumulative correct, not 10.
- [ ] ⚠️ `GET /api/daily-challenge` returns `daily_progress.target` **hardcoded to 10** (`DAILY_CORRECT_TARGET`), while the submit path uses the user's real `daily_goal`. If a user has goal 15/20, check the "X / 10 today" UI isn't showing the wrong target.
- [ ] ⚠️ Change the daily goal **mid-day** after already passing (e.g. 12 correct, goal was 10 → passed; raise goal to 20) → verify whether `passed`/streak behave sanely on the next submit (it recomputes `new_passed` against the new target).

### C3. Accumulation
- [ ] 6 correct then 4 correct (goal 10) → passes on the 2nd quiz; streak awarded once.
- [ ] 10 correct in the first quiz → passes immediately.
- [ ] Exactly at goal (10/10) and over goal (12) → both count as passed.
- [ ] Only **correct** answers count toward the tally; wrong answers don't.
- [ ] `today_total` accumulates alongside `today_correct`.

### C4. Cross-day reset
- [ ] Pass today, then it's a new SG day → daily progress resets to 0, fresh challenge available, not locked.
- [ ] `daily_challenges` rows are per (user, subject, date) — each day is independent; yesterday's pass doesn't leak into today.

### C5. Submit paths & validation
- [ ] Daily submitted via `/api/quiz/submit` with `quiz_type='daily'` vs `/api/daily-challenge/submit` → both credit daily progress / streak / XP consistently.
- [ ] Submit with `total <= 0` → rejected.
- [ ] Submit with `score > total` → handled gracefully (no negative/impossible XP).
- [ ] Two daily submits fired near-simultaneously → no double streak credit (idempotency holds).

---

## D. Cross-cutting
- [ ] Expired JWT (older than 24h) → all of the above endpoints return 401; app should bounce the user to login rather than silently failing.
- [ ] All three features behave the same whether the user reaches them from Home CTA, the nav, or a deep link.
- [ ] After any reward event, the navbar pills (XP/Level/rank/gems/freezes) match what the backend returns.
