# Trade Slip Automation

Private multi-broker dashboard for generating client trade slips from daily CSV ledgers.
PDFs are stored in a **private** Supabase bucket and opened via short-lived signed URLs.

Each broker only sees their own slips. The admin can invite friends from **Admin → Broker users**.

## Pages

| Path | Purpose |
|------|---------|
| `/login` | Sign in |
| `/dashboard` | Daily CSV upload, slip actions, single-day ZIP |
| `/history` | Past trade days, unsigned/signed counts, multi-day ZIP |
| `/account` | Profile + password |
| `/admin/users` | Invite / deactivate brokers (admin only) |

## Vercel env vars

- `SUPABASE_URL`
- `SUPABASE_KEY` (service role)
- `SUPABASE_ANON_KEY` (anon/public — required for login)
- `ADMIN_BOOTSTRAP_EMAIL` (first admin; auto-provisions `brokers` row on first login)
- `TEMPLATE_STORAGE_PATH=templates/blank-trade-slip.pdf`
- `ENVIRONMENT=production`

Legacy `ALLOWED_EMAIL` is still accepted as a fallback for `ADMIN_BOOTSTRAP_EMAIL`.

## Database setup

**New project:** run [`supabase/schema.sql`](supabase/schema.sql) in the Supabase SQL editor.

**Existing single-broker project:**

1. `python scripts/prepare_migration.py` (prints SQL with your admin UUID filled in)
2. Paste and run that SQL in the Supabase SQL editor
3. Redeploy the app
4. Sign in as the admin, then invite friends from `/admin/users`

Storage: private bucket `trade-slips`. Blank template at `templates/blank-trade-slip.pdf`.
New slip PDFs are stored under `{broker_id}/{year}/{month}/{day}/...`.
