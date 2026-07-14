#!/usr/bin/env bash
# Runs on the Proxmox HOST (murapa.me / pve-murapa).
# Creates a tiny unprivileged Debian LXC and installs SubManager inside it.
#
# Usage:
#   ./provision-lxc.sh <vmid> <hostname> <ip-last-octet> <config-file>
# Example:
#   ./provision-lxc.sh 307 submanager-pablo 37 config.pablo.yaml
set -euo pipefail

VMID="${1:?vmid required}"
HOSTNAME="${2:?hostname required}"
OCTET="${3:?ip last octet required}"
CONFIG_FILE="${4:?config file required}"

TEMPLATE="local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
STORAGE="local"
BRIDGE="vmbr1"
GW="172.16.1.1"
IP="172.16.1.${OCTET}/24"
NS="185.12.64.1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if pct status "$VMID" &>/dev/null; then
  echo "!! CT $VMID already exists, aborting." >&2
  exit 1
fi

echo "==> Creating tiny LXC $VMID ($HOSTNAME) at $IP"
pct create "$VMID" "$TEMPLATE" \
  --hostname "$HOSTNAME" \
  --cores 1 --memory 512 --swap 256 \
  --rootfs "${STORAGE}:2" \
  --net0 "name=eth0,bridge=${BRIDGE},gw=${GW},ip=${IP},type=veth" \
  --nameserver "$NS" \
  --unprivileged 1 --features nesting=1 \
  --onboot 1 --start 1

echo "==> Waiting for network"
for _ in $(seq 1 20); do
  pct exec "$VMID" -- getent hosts github.com &>/dev/null && break
  sleep 2
done

echo "==> Pushing config + installer"
pct exec "$VMID" -- mkdir -p /etc/submanager /root/deploy
pct push "$VMID" "$SCRIPT_DIR/$CONFIG_FILE" /etc/submanager/config.yaml
pct push "$VMID" "$SCRIPT_DIR/install-app.sh" /root/install-app.sh

echo "==> Running installer inside container"
pct exec "$VMID" -- bash /root/install-app.sh

echo
echo "==> CT $VMID provisioned."
echo "   Set the token:  pct exec $VMID -- sed -i 's#PUT_YOUR_TOKEN_HERE#<TOKEN>#' /etc/submanager/token.env"
echo "   Dry-run:        pct exec $VMID -- runuser -u submanager -- env GITHUB_TOKEN=<TOKEN> \\"
echo "                     SUBMANAGER_DATA_DIR=/var/lib/submanager \\"
echo "                     /opt/submanager/.venv/bin/python /opt/submanager/main.py \\"
echo "                     --config /etc/submanager/config.yaml --dry-run"
