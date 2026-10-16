#!/usr/bin/env bash
# Dump the Wiki.js database (users, groups, permissions, page history, settings).
# Page content is also in Git, but this is the only copy of everything else.
# Usage (from the docs-wiki folder):  ./scripts/backup.sh
# Restore: gunzip -c backups/wiki-YYYYmmdd-HHMM.sql.gz | docker compose exec -T db psql -U wikijs -d wiki
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
STAMP=$(date +%Y%m%d-%H%M)
docker compose exec -T db pg_dump -U wikijs -d wiki --clean --if-exists | gzip > "backups/wiki-$STAMP.sql.gz"
find backups -name 'wiki-*.sql.gz' -mtime +14 -delete
echo "$(date -Is) backup ok: backups/wiki-$STAMP.sql.gz"
# The server can be reclaimed by Oracle; copy these somewhere else now and then:
#   scp -i <key> ubuntu@<ip>:docs-wiki/backups/wiki-*.sql.gz .
