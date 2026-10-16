#!/usr/bin/env bash
# One-time setup for a fresh Oracle Cloud Ubuntu 22.04/24.04 instance.
# Run from the docs-wiki folder on the server:   sudo bash scripts/setup-server.sh
# Safe to re-run.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo."; exit 1; fi
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="${SUDO_USER:-ubuntu}"

echo "==> Updating packages"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git iptables-persistent

echo "==> Swap (needed on the 1 GB Micro shape, harmless elsewhere)"
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if ! swapon --show | grep -q /swapfile; then
  if (( MEM_MB < 4000 )); then
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    sysctl -w vm.swappiness=20 >/dev/null
    echo 'vm.swappiness=20' > /etc/sysctl.d/99-swappiness.conf
    echo "    added 4 GB swap (RAM is ${MEM_MB} MB)"
  else
    echo "    skipped (RAM is ${MEM_MB} MB)"
  fi
fi

echo "==> Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
usermod -aG docker "$APP_USER"
systemctl enable --now docker

echo "==> Opening ports 80/443 in the instance firewall"
# Oracle's Ubuntu images end the INPUT chain with a REJECT rule; insert above it.
open_port() {
  local proto=$1 port=$2
  if ! iptables -C INPUT -p "$proto" --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
    local pos
    pos=$(iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')
    if [[ -n "$pos" ]]; then
      iptables -I INPUT "$pos" -p "$proto" --dport "$port" -m state --state NEW -j ACCEPT
    else
      iptables -A INPUT -p "$proto" --dport "$port" -m state --state NEW -j ACCEPT
    fi
  fi
}
open_port tcp 80
open_port tcp 443
open_port udp 443
netfilter-persistent save

echo "==> Nightly database backup (03:15, keeps 14 days)"
chmod +x "$APP_DIR/scripts/backup.sh"
CRON_LINE="15 3 * * * cd $APP_DIR && ./scripts/backup.sh >> $APP_DIR/backups/backup.log 2>&1"
mkdir -p "$APP_DIR/backups"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR/backups"
( crontab -u "$APP_USER" -l 2>/dev/null | grep -v 'scripts/backup.sh' ; echo "$CRON_LINE" ) | crontab -u "$APP_USER" -

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  sed -i "s|^DB_PASS=.*|DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')|" "$APP_DIR/.env"
  chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "==> Created .env with a random DB_PASS. Check WIKI_DOMAIN in it."
fi

cat <<EOF

Done. Next:
  1. Log out and back in (so '$APP_USER' can run docker without sudo).
  2. Edit $APP_DIR/.env (WIKI_DOMAIN).
  3. Check DNS:   getent hosts \$(grep WIKI_DOMAIN .env | cut -d= -f2)   -> should print this server's public IP
  4. Start:       cd $APP_DIR && docker compose up -d
  5. Open https://<your domain> and create the admin account.
EOF
