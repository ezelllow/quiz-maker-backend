# Ooka — Session Handoff

_Last updated: 2026-06-14_

This is a handoff for a new chat. It explains the project, exactly where we are, the current feature, key files, important gotchas, and what to do next.

---

## 1. Project overview

**Ooka** (formerly "HabitGo") is a gamified O-Level Physics quiz/study app for Singapore students, by CuriousLab.

- **Frontend:** `C:\School\quiz-maker-frontend` — React 19 + Vite 8 + Tailwind + framer-motion. Dev server: `npm run dev` (http://localhost:5173).
- **Backend:** `C:\School\quizMaker\quiz_backend.py` — FastAPI + MySQL, single large file (~5400 lines). Run: `python quiz_backend.py` (http://localhost:8000). **No `--reload`**, so you must restart it to pick up code/.env changes.
- **Questions** come from Google Sheets (tabs `4E5N` + `Pure Physics`); Sheets creds via `credentials.json` in the backend folder.
- **Auth:** JWT (email/password + Google OAuth). Token lifetime is now **720h / 30 days** (`JWT_EXPIRATION_HOURS`).

---

## 2. CURRENT STAGE — what we just finished

We built and refined a **Duolingo-style avatar system** for the monkey mascot. Status: **working and building clean.** Most recent work was polishing the hoodie outfit.

### The avatar model
- **Base = the Ooka monkey** (uploaded cartoon monkey art), not a photo. Photo upload was removed.
- **Skin tone (free):** 6 recolored fur tones — `skin_default` (Classic Brown), `skin_tan`, `skin_espresso`, `skin_grey` (Silver), `skin_golden`, `skin_cream`. Stored in `equipped.skin`, default `skin_default`. Switchable for free in the Shop.
- **Outfit (free for now):** one item, **Ooka Hoodie** (`hoodie_navy`), a navy hoodie PNG layered over the monkey's torso/arms. Stored in `equipped.outfit`. Toggle on/off.
- **Legacy emoji wearables** (hats/glasses/hands/legs/accessory/frames) still exist in code but are **NOT rendered and NOT sold** — they were removed from the shop and from the avatar render (user asked to remove a leftover hat).

### The Shop (`ShopPage.jsx`) currently shows
1. "Your Ooka" live preview (full-body monkey wearing current skin + outfit).
2. **Skin tone** section — 6 free tone cards (tap to wear).
3. **Outfits** section — the hoodie card shows **just the hoodie image** (it's the item on sale), with a Wear / "Worn — tap to remove" toggle.
- All "buyable" (gem-cost) items were removed earlier; the backend `/api/shop` only returns free items (`cost == 0`). The gem/earn/lock UI auto-hides when nothing is buyable (`hasBuyables`).

---

## 3. Key files & what they do

### Frontend
- `src/components/ui/Avatar.jsx` — **the core of the avatar system.**
  - `AVATAR_REGISTRY` — skin id -> head + full PNG paths. **Paths currently point to `/brand/ooka/avatars/body_skin_tones/skin_*.png`** (see gotcha #5).
  - `OUTFIT_REGISTRY` — `hoodie_navy` -> `{ src: '/brand/ooka/avatars/outfit_hoodie_v2.png', width: 1.08, top: 0.28 }`. `width`/`top` are the only two numbers to tweak hoodie fit.
  - Two render variants: `variant="head"` (circular crop — header/leaderboard/profile) and `variant="full"` (whole body — Shop preview). The outfit only renders in `full` variant and is rendered **outside** the clipped disc so sleeves aren't cut.
- `src/components/ShopPage.jsx` — Skin tone + Outfits sections; "Your Ooka" preview; buy/equip logic via `/api/shop`, `/api/shop/redeem`, `/api/shop/equip`.
- `src/components/EditProfileModal.jsx` — name editing only (photo upload removed; shows the equipped avatar).
- Avatar is also shown in `Layout.jsx` (header), `HomePage.jsx`, `LeaderboardPage.jsx`, `Settings.jsx` — all pass `equipped`.

### Backend (`quiz_backend.py`)
- `SHOP_CATALOGUE` (~line 4595) — items. Skin tones (slot `skin`, free) + Ooka Hoodie (slot `outfit`, free). Old emoji wearables still listed but filtered out of the shop.
- `_WEARABLE_SLOTS = {"skin", "outfit", "hat", "glasses", "accessory", "frame", "hands", "legs"}`.
- `/api/shop` returns only free items: `visible_catalogue = [it for it in SHOP_CATALOGUE if int(it.get("cost",0)) == 0]`. **To re-enable selling wearables, remove this filter.**
- `/api/shop/equip` — equips by slot; free items (cost 0) equippable without ownership.
- `JWT_EXPIRATION_HOURS = 720`.

### Assets — `C:\School\quiz-maker-frontend\public\brand\ooka\avatars\`
- `body_skin_tones/skin_{default,tan,espresso,grey,golden,cream}_{head,full}.png` — the 12 skin PNGs (user moved them into this subfolder).
- `outfit_hoodie_v2.png` — the current hoodie asset (the one in use).
- `avatar_head.png`, `avatar_full_body.png` — the original monkey source art.
- Leftover/unused: `outfit_hoodie.png`, `outfit_hoodie_new.png`, old `ooka_*` skins. Harmless.

---

## 4. How the monkey art was processed (for regeneration)

If you ever need to regenerate skin tones or fix the cutout (done with Python + PIL + scipy in the sandbox):
- **Background removal:** flood-fill white/black bg connected to the image border -> transparent.
- **Halo fix:** erode the outer silhouette ~2px to kill the white anti-alias fringe (it glows on dark backgrounds). **Do NOT remove enclosed white regions globally** — the eye glints are enclosed white and must stay.
- **Body white slivers:** the gaps between arms and torso are enclosed white; remove white **only below the neck (y > ~40%)** so eye glints (above neck) survive.
- **Skin tones:** recolor warm-hue (fur+skin) pixels in HSV; keep outlines/eyes/mouth. Tones: tan (lighter), espresso (darker), grey (desaturate), golden (hue shift), cream (pale).
- **Hoodie fit:** overlay PNG centered on torso; current best = `width 1.08, top 0.28`, rendered unclipped.

---

## 5. IMPORTANT GOTCHAS (read before editing)

1. **File-tool truncation + null bytes:** The Edit/Write tools on large files (`quiz_backend.py`, `ShopPage.jsx`, `QuizMaker.jsx`, `Avatar.jsx`) have repeatedly (a) truncated the file tail and (b) injected NUL bytes. **After every edit to these files, verify:** `python3 -c "print(open(F,'rb').read().count(0))"` (should be 0) and run an esbuild / `ast.parse` check. To repair: strip NULs and rebuild the truncated tail (many examples in this session). Prefer small, well-anchored edits; for big rewrites use a Python heredoc that writes the whole tail.
2. **Build check (frontend):** `cd quiz-maker-frontend && npx esbuild src/main.jsx --bundle --loader:.jsx=jsx --loader:.js=jsx --loader:.css=empty --jsx=automatic --packages=external --format=esm --outfile=/dev/null` — must be 0 errors. (`npx vite build` fails in the sandbox due to a rolldown native-module issue — environmental, not your code.)
3. **Build check (backend):** `python3 -m py_compile quiz_backend.py`.
4. **Mount blocks overwrite/delete:** the sandbox mount can't overwrite or delete existing files in the user's folder (and sometimes desyncs — files appear/disappear between bash calls). Workarounds: write NEW filenames (that's why it's `outfit_hoodie_v2.png`), copy to `/tmp` for stable processing, and retry on transient "No such file" errors.
5. **Asset path coupling:** skin PNGs live in `body_skin_tones/` and `AVATAR_REGISTRY` points there. If assets move again, update the registry paths. Keep assets in a stable folder.
6. **Restart + cache:** Backend code/.env changes need a backend restart. Frontend asset/PNG changes need a hard refresh (Ctrl+Shift+R) since filenames are reused.

---

## 6. Recent incident (resolved) — `.env`

The `.env` got accidentally overwritten with personal interview notes, wiping all env vars -> Google login 500s and 401s everywhere. **Resolved:** restored `.env` from git history (`git show dbc03c7:.env > .env`), which recovered DB creds, `JWT_SECRET`, `GOOGLE_CLIENT_ID/SECRET`; `credentials.json` already covers Sheets; other keys have working defaults. The notes were preserved to `C:\School\quizMaker\climbing_talk_notes.md`. `DB_PASSWORD` in the restored `.env` is empty (matches the original) — if MySQL access is denied, that's where to add a password.

---

## 7. What's NOT done / possible next steps

- Hoodie (and any outfit) only shows on the **full-body** monkey (Shop preview). The small **circular** header/leaderboard avatars only show the head, so the hoodie doesn't appear there. Could make those head-and-shoulders to show a collar.
- Outfits/skins are currently **free**. If the user wants real purchasing, re-enable the `/api/shop` cost filter + the `hasBuyables` UI and set gem prices.
- More outfits/wearable assets can be added the same way (new PNG -> `OUTFIT_REGISTRY` entry + catalogue item, slot `outfit`, calibrate `width`/`top`).
- Optional cleanup: delete unused asset files (`outfit_hoodie.png`, `outfit_hoodie_new.png`, old `ooka_*`) — but the mount blocks deletes, so the user must do it manually.

---

## 8. Immediate state right now

- Frontend builds clean (0 esbuild errors), backend `py_compile` clean, no NUL corruption in touched files.
- Latest changes the user should verify after **restarting backend + hard refresh**:
  - Skin-tone cards render (paths fixed to `body_skin_tones/`).
  - The leftover hat is gone.
  - Hoodie fits the monkey better (sleeves cover arms, head pokes out).
  - Shop Outfits card shows just the hoodie image.
- If the user reports the fit is still slightly off, adjust `OUTFIT_REGISTRY.hoodie_navy.width` / `.top` in `Avatar.jsx`.
