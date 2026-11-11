#!/usr/bin/env bash
# Pull the latest main and restart the stack. Used by GitHub Actions and by hand.
set -euo pipefail
cd "$HOME/pmh"
git fetch origin main && git reset --hard origin/main
cd deploy
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
docker image prune -f >/dev/null
docker compose -f docker-compose.prod.yml ps
