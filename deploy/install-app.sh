#!/usr/bin/env bash
# Runs INSIDE a fresh Debian LXC container (as root).
# Installs SubManager as a hardened systemd oneshot + timer running under an
# unprivileged 'submanager' user. Idempotent: safe to re-run.
set -euo pipefail

APP_DIR=/opt/submanager
CFG_DIR=/etc/submanager
REPO=${SUBMANAGER_REPO:-https://github.com/murapadev/SubManager.git}
BRANCH=${SUBMANAGER_BRANCH:-main}

echo "==> Installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends git python3 python3-venv python3-pip ca-certificates >/dev/null

echo "==> Creating service user"
id submanager &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin submanager

echo "==> Fetching application ($REPO@$BRANCH)"
# The repo is owned by the unprivileged 'submanager' user but git runs here as
# root; without this, re-runs fail with "detected dubious ownership".
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  rm -rf "$APP_DIR"
  git clone --depth 1 -b "$BRANCH" "$REPO" "$APP_DIR"
fi

echo "==> Python venv + dependencies"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> Config + token scaffolding"
mkdir -p "$CFG_DIR"
# config.yaml is placed by the host provisioner; only scaffold token file here.
if [ ! -f "$CFG_DIR/token.env" ]; then
  echo 'GITHUB_TOKEN=PUT_YOUR_TOKEN_HERE' > "$CFG_DIR/token.env"
fi
chmod 600 "$CFG_DIR/token.env"
[ -f "$CFG_DIR/config.yaml" ] && chmod 640 "$CFG_DIR/config.yaml"
chown -R submanager:submanager "$APP_DIR" "$CFG_DIR"

echo "==> systemd units"
install -m 644 "$APP_DIR/deploy/submanager.service" /etc/systemd/system/submanager.service
install -m 644 "$APP_DIR/deploy/submanager.timer"   /etc/systemd/system/submanager.timer
systemctl daemon-reload
systemctl enable --now submanager.timer

echo "==> Done. Timer status:"
systemctl status submanager.timer --no-pager --lines=0 || true
echo
echo "Next: put the real token in $CFG_DIR/token.env, then dry-run:"
echo "  runuser -u submanager -- env GITHUB_TOKEN=\$(grep -oP '(?<=GITHUB_TOKEN=).*' $CFG_DIR/token.env) \\"
echo "    $APP_DIR/.venv/bin/python $APP_DIR/main.py --config $CFG_DIR/config.yaml --dry-run"
