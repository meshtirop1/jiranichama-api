# JiraniChama API

Django backend for JiraniChama: a chama app that records every shilling, enforces every
rule, and never touches the money. The design is the *JiraniChama Architecture Proposal
v1.1*; this repo is at **Phase 0 (foundations)**.

Start with [docs/README.md](docs/README.md).

## Run it locally

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt pytest pytest-django ruff
cp .env.example .env        # then fill in the keys (instructions inside)
docker compose up -d db redis
.venv/Scripts/python manage.py migrate
.venv/Scripts/python -m pytest
docker compose down         # stop the stack when you are done
```

## What is here

| Area | Where |
| --- | --- |
| Settings, URLs, Celery | `config/` |
| Canonical JSON, encryption, outbox, problem details | `apps/core/` |
| Append-only, hash-chained ledger and platform signing | `apps/ledger/` |
| Phone sign-in (SMS code, PIN, tokens) | `apps/accounts/` |
| SMS with provider fallback | `apps/notify/` |
| Feature flags with pilot allowlist | `apps/flags/` |
| Chamas and memberships (minimal, grows in Phase 1) | `apps/chamas/` |
| Staff console (two-factor), chained audit log | `apps/staff/` |
| Shared hash test vectors (also used by the Flutter app) | `vectors/` |
