# The test server

A test copy of the API for trying the app on real phones, without a cable or an SMS
provider. It runs on the shared VPS (167.233.160.177) next to the other sites. It is not
production: there is no real money and the data can be reset.

- **Address**: https://jiranichama.167-233-160-177.sslip.io (sslip.io turns the name into
  the server's IP, so no DNS record is needed; Let's Encrypt gives it a real certificate).
  The admin console is at `/console/`.
- **Processes** (systemd): `jiranichama-web` (gunicorn on 127.0.0.1:8111),
  `jiranichama-celery` (worker), `jiranichama-beat` (scheduler). Each is capped at 600 MB.
- **Data**: native PostgreSQL (database and role `jiranichama`) and Redis (db 5), both on
  localhost only. Files in `/home/deploy/sites/jiranichama-data`. One database role here;
  production uses the two-role setup in [database roles](database-roles.md).
- **Settings**: `/home/deploy/sites/jiranichama.env`, written once by the setup script with
  fresh keys (secret key, field encryption, HMAC, platform signing key). Not in git.
- **It never touches** the JiraniSoko Docker stack or the other sites.

## Signing in without SMS

There is no SMS provider yet. The four demo phones (0700 000 001 to 004) sign in, approve
and vote with the fixed code in `DEMO_SIGNIN_CODE`, and get no SMS. Every other number's
messages, sign-in codes included, are written to `jiranichama-data/sms.log` (read it as
root to sign in with any other number). A startup check (`jirani.W001`) warns
wherever the demo code is on. **Production never sets `DEMO_SIGNIN_CODE`.**

## Setting it up or updating it

As root on the server:

```bash
cd /home/deploy/sites/jiranichama && git pull && bash deploy/test-server/setup.sh
```

To try a branch before it is merged: `JC_BRANCH=my-branch bash deploy/test-server/setup.sh`.
Run it again without `JC_BRANCH` to go back to `main`. The script keeps HTTPS on every run
(it reinstalls the certificate after rewriting the nginx site) and runs from a copy of
itself, because pulling the code changes the script file.

The script installs what is missing, pulls the code (`JC_BRANCH`, default `main`),
installs Python 3.12 for the app with `uv` (the system Python is left alone), migrates,
collects static files, (re)starts the three services, and sets up nginx and HTTPS.

Demo data:

```bash
sudo -u deploy bash -c 'set -a; . /home/deploy/sites/jiranichama.env; set +a; cd /home/deploy/sites/jiranichama && .venv/bin/python manage.py seed_demo'
```

## The app for it

Build with the test server's address and install it:

```bash
flutter build apk --release --dart-define=API_BASE_URL=https://jiranichama.167-233-160-177.sslip.io/api/v1
```

## Worker queues (2026-10-20)

The test-server worker must consume every queue in `CELERY_TASK_ROUTES`: `celery,feeds,rules,notify,documents,signing`. It used to consume only `celery`, so platform signatures never ran there ("207 records waiting for JiraniChama's signature"). Production workers need the same list (or one worker per queue).
