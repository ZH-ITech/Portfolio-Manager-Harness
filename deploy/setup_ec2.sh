#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu 24.04 EC2 instance (Jira PMH-19).
# Usage (on the server):  bash setup_ec2.sh https://github.com/ZH-ITech/Portfolio-Manager-Harness.git
set -euo pipefail
REPO_URL="${1:?usage: bash setup_ec2.sh <git clone URL>}"
APP_DIR="$HOME/pmh"

echo "==> Installing Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

echo "==> Adding 2 GB swap (helps small instances build images)"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Getting the code"
if [ ! -d "$APP_DIR/.git" ]; then git clone "$REPO_URL" "$APP_DIR"; fi
cd "$APP_DIR" && git pull --ff-only

[ -f .env ] || { cp .env.example .env; chmod 600 .env; }
[ -f deploy/.env ] || cp deploy/.env.example deploy/.env

cat <<MSG

==> Almost done. Two files to edit, then start:
  1) nano $APP_DIR/.env          set LLM_API_KEY (a separate key for the server) and
                                 PTC_SHARED_SECRET (any long random text)
  2) nano $APP_DIR/deploy/.env   set DOMAIN (e.g. 13-201-5-7.sslip.io from your Elastic IP)
  3) newgrp docker   (or log out and back in), then:
     cd $APP_DIR/deploy && docker compose -f docker-compose.prod.yml up -d --build
MSG
