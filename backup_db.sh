#!/usr/bin/env sh

set -e  # exit immediately on error

# ─────────────────────────────────────────────
# Load environment variables
# ─────────────────────────────────────────────
if [ ! -f .env ]; then
  echo ".env file not found"
  exit 1
fi

set -a
. ./.env
set +a

# ─────────────────────────────────────────────
# Variables
# ─────────────────────────────────────────────
CONTAINER_NAME="mariadb-container"
BACKUP_DIR="/root/db_backups"
DATE_STAMP="$(date +%Y_%m_%d_%H_%M_%S)"
BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}-${DATE_STAMP}.sql"

# ─────────────────────────────────────────────
# Prepare backup directory
# ─────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

# ─────────────────────────────────────────────
# Run mysqldump inside Docker
# ─────────────────────────────────────────────
echo "Starting database backup..."
docker exec "$CONTAINER_NAME" \
  mysqldump -u root -p"$DB_ROOT_PASSWORD" "$DB_NAME" \
  > "$BACKUP_FILE"

# ─────────────────────────────────────────────
# Optional: compress backup
# ─────────────────────────────────────────────
gzip "$BACKUP_FILE"

echo "✅ Backup completed:"
echo "   ${BACKUP_FILE}.gz"