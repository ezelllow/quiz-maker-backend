# Migrating the backend: Render → Railway

Goal: backend runs in the SAME Railway project as the MySQL DB (Southeast Asia
— Singapore), talking to it over Railway's private network. Frontend stays on
Cloudflare Pages. The DB does not move — zero data migration.

---

## Phase 0 — Before you start (5 min)

1. Push the backend repo (`C:\School\quizMaker` → github.com/ezelllow/quiz-maker-backend).
   The repo now contains `railway.toml` (start command + `/health` healthcheck) — Railway reads it automatically.
2. In the **Render dashboard**, open the current service → Environment, and copy
   these values somewhere safe (you'll re-enter them in Railway):
   - `JWT_SECRET`  ← copy the SAME value (existing student logins stay valid)
   - `GOOGLE_SERVICE_ACCOUNT_JSON` (the full JSON blob)
   - `GOOGLE_CLIENT_ID`
   - `CORS_ORIGINS`

## Phase 1 — Create the service on Railway (10 min)

1. Open your existing Railway **project** (the one with the MySQL service).
2. Click **+ Create → GitHub Repo** → pick `ezelllow/quiz-maker-backend`.
   (First time: authorize Railway's GitHub app for that repo.)
3. Before the first deploy finishes, open the new service → **Settings**:
   - **Region**: Southeast Asia (Singapore) — MUST match the MySQL service.
   - Serverless/App Sleeping: leave **OFF** (we want always-on).

## Phase 2 — Environment variables (10 min)

Service → **Variables** → Raw editor. Use Railway *reference variables* for the
DB so credentials stay in one place (replace `MySQL` with your DB service's
exact name if different):

```
DB_HOST=${{MySQL.RAILWAY_PRIVATE_DOMAIN}}
DB_PORT=3306
DB_USER=${{MySQL.MYSQLUSER}}
DB_PASSWORD=${{MySQL.MYSQLPASSWORD}}
DB_NAME=${{MySQL.MYSQLDATABASE}}
JWT_SECRET=<same value as on Render>
GOOGLE_CLIENT_ID=<from Render>
GOOGLE_SERVICE_ACCOUNT_JSON=<paste the full JSON>
CORS_ORIGINS=https://habitgo.curiouslab.sg,http://localhost:5173
SHEET_NAMES=Pure Physics,combinedG1,combinedG2,combinedG3
```

Notes:
- `DB_PORT` is **3306** on the private network — do NOT use `MYSQLPORT`
  (that's the public TCP-proxy port, a different number).
- `RAILWAY_PRIVATE_DOMAIN` resolves to something like `mysql.railway.internal`
  — traffic never leaves the datacenter (faster + no egress charges).
- `PUBLIC_BASE_URL` is set in Phase 3 (needs the domain first). Until it's set,
  image URLs will be wrong — that's expected during setup.

## Phase 3 — Domain + PUBLIC_BASE_URL (5 min)

1. Service → Settings → **Networking → Generate Domain** → you get
   `something.up.railway.app`.
2. Add variable: `PUBLIC_BASE_URL=https://something.up.railway.app`
   (this is baked into question image URLs — it must be exact).
3. Redeploy (Railway redeploys on variable change automatically).

Optional but recommended — custom domain `api.curiouslab.sg`:
1. Service → Settings → Networking → **+ Custom Domain** → `api.curiouslab.sg`
   → Railway shows a CNAME target.
2. Cloudflare dashboard → curiouslab.sg → DNS → add **CNAME** `api` → that
   target. Start with **DNS only** (grey cloud); you can turn the proxy on
   later (needs SSL mode "Full" if you do).
3. Once it resolves, change `PUBLIC_BASE_URL=https://api.curiouslab.sg`.
   Using your own domain means future host moves never break image URLs again.

## Phase 4 — Verify the backend (10 min)

Watch the deploy logs, expect:
- "Initializing database..." then "Pre-loading file map in background thread..."
- "Loaded tabs — Pure Physics: N rows · combinedG1: ..." with **no** 4E5N error.

Then test in a browser (replace with your domain):
- `https://<domain>/health` → ok
- `https://<domain>/api/subtopics?level=pure` → topic list JSON
- `https://<domain>/docs` → interactive API docs; try `/api/availability?level=combinedG2`

## Phase 5 — Point the frontend at Railway (10 min)

1. Cloudflare Pages → your project → Settings → **Environment variables** →
   set `VITE_API_BASE_URL=https://api.curiouslab.sg` (or the railway.app domain).
2. Also update `C:\School\quiz-maker-frontend\.env` locally to match for dev
   parity (local dev still uses localhost:8000 — only change it if you want
   the dev build hitting Railway).
3. **Retrigger a Pages build** (Deployments → Retry / push any commit) —
   Vite bakes the URL in at build time; changing the var without rebuilding
   does nothing.
4. Full smoke test on https://habitgo.curiouslab.sg:
   login → home loads → build a quiz WITH diagram images → submit → XP/streak
   → leaderboard → shop.

## Phase 6 — Decommission Render (after 2-3 quiet days)

1. Render dashboard → the service → **Suspend** (don't delete yet — free rollback).
2. A week later, delete the service. Optionally delete `render.yaml` from the repo.

## Cost guardrails

- Project → Settings → **Usage Limits**: set a hard cap (e.g. $25/mo) and an
  email alert at $10 so a bug can't surprise you.
- Expected: roughly $10–18/mo total for backend + MySQL at your scale
  (Hobby plan $5 includes $5 of usage; RAM $10/GB-mo, CPU $20/vCPU-mo).

## If something breaks

- **DB connection refused**: check DB_PORT=3306 and that both services are in
  the same project + region; as a temporary fallback use the MySQL service's
  public `MYSQLHOST`/`MYSQLPORT` values (works from anywhere, slower).
- **Images 404**: `PUBLIC_BASE_URL` doesn't match the domain you're actually
  serving from — fix and redeploy.
- **CORS errors**: `CORS_ORIGINS` must contain `https://habitgo.curiouslab.sg`
  exactly (scheme included, no trailing slash).
- **Rollback**: flip `VITE_API_BASE_URL` back to the Render URL and rebuild
  Pages — Render service is still there while suspended.
