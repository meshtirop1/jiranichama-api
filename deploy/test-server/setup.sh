#!/usr/bin/env bash
# Sets up (or updates) the JiraniChama TEST server on the shared VPS. Run as root:
#   bash /home/deploy/sites/jiranichama/deploy/test-server/setup.sh
# Safe to run again: every step checks what is already there. It never touches the
# JiraniSoko Docker stack or any other site. See docs/operations/test-server.md.
set -euo pipefail

APP=/home/deploy/sites/jiranichama
DATA=/home/deploy/sites/jiranichama-data
HOST=${JC_HOST:-jiranichama.167-233-160-177.sslip.io}
PORT=8111
REPO=https://github.com/meshtirop1/jiranichama-api.git
BRANCH=${JC_BRANCH:-main}
U=deploy
cd /home/$U  # commands run as deploy must not start in root's folder

echo "== packages (native PostgreSQL and Redis, bound to localhost only)"
need=()
command -v pg_ctlcluster >/dev/null || need+=(postgresql)
command -v redis-server >/dev/null || need+=(redis-server)
command -v git >/dev/null || need+=(git)
if [ ${#need[@]} -gt 0 ]; then
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${need[@]}"
fi
systemctl enable --now postgresql redis-server >/dev/null

echo "== code"
if [ -d "$APP/.git" ]; then
  sudo -u $U git -C "$APP" fetch -q origin "$BRANCH"
  sudo -u $U git -C "$APP" reset -q --hard "origin/$BRANCH"
else
  sudo -u $U git clone -q -b "$BRANCH" "$REPO" "$APP"
fi
mkdir -p "$DATA/media" && chown -R $U:$U "$DATA"

echo "== Python 3.12 (its own, via uv; the system Python is left alone)"
if [ ! -x /home/$U/.uvtool/bin/uv ]; then
  sudo -u $U python3 -m venv /home/$U/.uvtool
  sudo -u $U /home/$U/.uvtool/bin/pip install -q uv
fi
UV=/home/$U/.uvtool/bin/uv
if [ ! -x "$APP/.venv/bin/python" ]; then
  sudo -u $U "$UV" venv -q --python 3.12 "$APP/.venv"
fi
sudo -u $U "$UV" pip install -q --python "$APP/.venv/bin/python" -r "$APP/requirements.txt"

echo "== database"
if ! sudo -u postgres psql -tAc "select 1 from pg_roles where rolname='jiranichama'" | grep -q 1; then
  DBPASS=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
  sudo -u postgres psql -qc "create role jiranichama login password '$DBPASS'"
  sudo -u postgres psql -qc "create database jiranichama owner jiranichama"
  echo "$DBPASS" > /root/.jiranichama-dbpass && chmod 600 /root/.jiranichama-dbpass
fi
DBPASS=$(cat /root/.jiranichama-dbpass)

echo "== settings (written once; edit $APP/../jiranichama.env to change)"
ENVF=/home/deploy/sites/jiranichama.env
if [ ! -f "$ENVF" ]; then
  sudo -u $U "$APP/.venv/bin/python" - "$ENVF" "$HOST" "$DBPASS" "$DATA" <<'PY'
import secrets, sys
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
path, host, dbpass, data = sys.argv[1:]
pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()).decode().replace("\n", "\\n")
lines = {
    "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
    "DJANGO_DEBUG": "0",
    "DJANGO_ALLOWED_HOSTS": host,
    "DJANGO_SECURE_SSL_REDIRECT": "1",
    "DATABASE_URL": f"postgres://jiranichama:{dbpass}@127.0.0.1:5432/jiranichama",
    "REDIS_URL": "redis://127.0.0.1:6379/5",
    "FIELD_ENCRYPTION_KEYS": Fernet.generate_key().decode(),
    "HMAC_KEY": secrets.token_urlsafe(32),
    "PLATFORM_SIGNER": "local",
    "PLATFORM_SIGNING_KEY_PEM": pem,
    "MEDIA_ROOT": f"{data}/media",
    "VERIFY_BASE_URL": f"https://{host}",
    # No SMS provider on the test server: messages go to a file staff can read.
    "SMS_BACKENDS": "apps.notify.sms.DevFileBackend",
    "DEV_SMS_FILE": f"{data}/sms.log",
    # Test server only: these demo phones type the fixed code instead of an SMS.
    "DEMO_SIGNIN_CODE": "123456",
    "DEMO_PHONES": "+254700000001,+254700000002,+254700000003,+254700000004",
    "CELERY_TASK_ALWAYS_EAGER": "0",
}
with open(path, "w") as f:
    for k, v in lines.items():
        f.write(f'{k}="{v}"\n' if k == "PLATFORM_SIGNING_KEY_PEM" else f"{k}={v}\n")
PY
  chmod 600 "$ENVF"
fi

echo "== migrate and static files"
run() { sudo -u $U bash -c "set -a; . $ENVF; set +a; cd $APP && .venv/bin/python manage.py $*"; }
run migrate --noinput -v 0
run collectstatic --noinput -v 0

echo "== services"
for unit in jiranichama-web jiranichama-celery jiranichama-beat; do
  install -m 644 "$APP/deploy/test-server/$unit.service" "/etc/systemd/system/$unit.service"
done
systemctl daemon-reload
systemctl enable -q jiranichama-web jiranichama-celery jiranichama-beat
systemctl restart jiranichama-web jiranichama-celery jiranichama-beat

echo "== nginx and HTTPS"
sed "s/__HOST__/$HOST/g; s/__PORT__/$PORT/g" "$APP/deploy/test-server/nginx.conf" \
  > /etc/nginx/sites-available/jiranichama
ln -sf /etc/nginx/sites-available/jiranichama /etc/nginx/sites-enabled/jiranichama
nginx -t -q && systemctl reload nginx
if [ ! -d "/etc/letsencrypt/live/$HOST" ]; then
  certbot --nginx -n --agree-tos --register-unsafely-without-email -d "$HOST" --redirect -q
fi

sleep 2
curl -fsS -o /dev/null -w "health: %{http_code}\n" "https://$HOST/healthz" || \
  curl -sS -o /dev/null -w "root: %{http_code}\n" "https://$HOST/"
echo "== done: https://$HOST"
