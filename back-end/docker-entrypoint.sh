#!/bin/sh
set -e

echo "[AIInvest Backend] Starting container initialization..."

if [ -n "$DATABASE_URL" ]; then
  echo "[AIInvest Backend] Running Prisma database migrations (prisma migrate deploy)..."
  if [ -f "./node_modules/.bin/prisma" ]; then
    ./node_modules/.bin/prisma migrate deploy
  else
    npx prisma migrate deploy
  fi
  echo "[AIInvest Backend] Database migrations applied successfully."
else
  echo "[AIInvest Backend] WARNING: DATABASE_URL not set, skipping migrations."
fi

echo "[AIInvest Backend] Launching application..."
exec "$@"
