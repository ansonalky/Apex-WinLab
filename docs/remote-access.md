# Temporary remote access

For letting someone outside this machine try the dashboard. The local demo
(`app.py` on port 5050, no login) is not changed by any of this.

## What remote mode does

`app.py --remote-auth data/remote_access/auth.json` starts a separate
instance that:

- requires one login (HTTP Basic) for every request: the page, static
  files, every `/api/...` route and `/stats`. Unknown paths also answer 401;
- redirects plain HTTP to HTTPS before any login prompt, so the password
  is never sent unencrypted, and sends HSTS on HTTPS responses;
- allows one new AI review (situation or decision) at a time, and at most
  `--remote-max-reviews` (default 20) per UTC day. The count is kept in
  `data/remote_access/usage.json`, so a restart does not reset it, and a
  failed call still counts. Reviews already in the cache open without
  counting. Refusals appear in the coach panel as "busy" (retryable) or
  "limit";
- turns off Overstat URL imports (`/api/open-match` answers 403);
- serves with waitress, not Flask's development server. Debug mode is
  never on.

Gemini and database credentials stay in the server's environment (`.env`).
No route returns their values: `/api/coach/status` names the variable
only, and errors are scrubbed (`coach._redact`, `import_tiger.redact`).

## Start

```sh
.venv/bin/python remote_access.py init        # once; writes data/remote_access/ (gitignored)
set -a; . ./.env; set +a
.venv/bin/python app.py --data-dir data --expansion-dir ~/Apex-WinLab-expansion/data \
    --port 5051 --backend tiger --remote-auth data/remote_access/auth.json --remote-max-reviews 20
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:5051
```

`init` writes `auth.json`, which holds a salted SHA-256 of a random 128-bit
password and is the only file the server reads. It also writes
`credentials.txt` (mode 600), the plain login to share. Read that file with
`cat data/remote_access/credentials.txt`. Running `init` again replaces the
login, and the old password stops working at the next server start.

The tunnel is a Cloudflare quick tunnel. It needs no account, writes no
tunnel credentials, and prints a random `https://….trycloudflare.com` URL
that changes on every start. It forwards only to port 5051, so the
login-free local demo is not reachable through it.

## Stop

1. Stop `cloudflared` (Ctrl-C, or `pkill -f "cloudflared tunnel"`). The URL
   stops working immediately and is never reused.
2. Stop the port-5051 server (Ctrl-C, or
   `pkill -f "app.py.*--remote-auth"`).
3. Optionally delete the login: `rm -r data/remote_access`. Without
   `auth.json`, remote mode refuses to start.
