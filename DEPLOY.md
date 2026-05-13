# Deployment

Production stack:
- **Frontend** → Cloudflare Pages (free)
- **Backend** → Render (Singapore, $7/mo Starter)
- **Database** → Railway MySQL (~$5/mo)

This repo is wired up so you can deploy without code changes. All environment-specific values come from env vars set on each platform.

---

## Backend env vars (set in Render dashboard)

| Key | Value | Notes |
|---|---|---|
| `DB_HOST` | (from Railway) | |
| `DB_PORT` | (from Railway) | usually 3306 or a 5-digit port |
| `DB_USER` | (from Railway) | |
| `DB_PASSWORD` | (from Railway) | |
| `DB_NAME` | (from Railway) | usually `railway` |
| `JWT_SECRET` | random 48-char string | generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `GOOGLE_CLIENT_ID` | (from Google Cloud Console) | |
| `GOOGLE_CLIENT_SECRET` | (from Google Cloud Console) | |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | **full contents** of your service-account JSON file | paste the entire file body as one value |
| `PUBLIC_BASE_URL` | `https://<your-service>.onrender.com` | what the backend uses to build absolute image URLs |
| `CORS_ORIGINS` | `https://<your-frontend>.pages.dev,https://<your-domain>` | comma-separated allow-list |

---

## Frontend env vars (set in Cloudflare Pages dashboard)

| Key | Value |
|---|---|
| `VITE_API_BASE_URL` | the Render URL (e.g. `https://quiz-backend-xxxx.onrender.com`) |
| `VITE_GOOGLE_CLIENT_ID` | the same Google OAuth client ID as the backend |
| `NODE_VERSION` | `20` |

> Vite inlines `VITE_*` vars at **build time**, so you must redeploy after changing them.

---

## Cloudflare Pages build settings

- Framework preset: **Vite**
- Build command: `npm run build`
- Build output: `dist`
- Root directory: leave blank (or `quiz-maker-frontend` if you're using a monorepo)

---

## Google OAuth — production origins

In Google Cloud Console → APIs & Services → Credentials → your OAuth client → **Authorised JavaScript origins**, add:
- `https://<your-frontend>.pages.dev`
- (and your custom domain if you set one)

Wait ~1 minute after saving for the change to propagate.

---

## Ordered deploy steps (first time)

1. Push latest code to GitHub.
2. Provision **Railway MySQL**. Copy host/port/user/password/db name.
3. (Optional) Migrate local data: `mysqldump quiz_maker > dump.sql` then `mysql -h <host> -P <port> -u <user> -p<pwd> <db> < dump.sql`.
4. Create **Render Web Service** from the `quizMaker` repo. The included `render.yaml` sets the build/start commands automatically. Region: Singapore. Plan: Starter.
5. Fill in all env vars in Render. Leave `PUBLIC_BASE_URL` and `CORS_ORIGINS` blank for now.
6. Wait for deploy. Copy the Render URL.
7. Set `PUBLIC_BASE_URL` to that URL and save (it redeploys).
8. Create **Cloudflare Pages** project from the `quiz-maker-frontend` repo. Set the env vars above.
9. Wait for deploy. Copy the Cloudflare URL.
10. Back in Render → set `CORS_ORIGINS` to the Cloudflare URL → save → backend redeploys.
11. In Google Cloud Console, add the Cloudflare URL to authorised JavaScript origins.
12. Test: open the Cloudflare URL → sign in → create a quiz.

---

## Local dev still works

Without any env vars set:
- `DB_HOST` defaults to `localhost`
- `PUBLIC_BASE_URL` defaults to `http://localhost:8000`
- `CORS_ORIGINS` defaults to `http://localhost:5173,http://localhost:3000`
- Service-account credentials fall back to `credentials.json` in the script directory

So your local workflow is unchanged.
