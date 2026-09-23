#!/bin/sh
set -e

echo "[SAG API] Starting SAG container initialization..."

if [ -f "alembic.ini" ]; then
  echo "[SAG API] Running Alembic database migrations (alembic upgrade head)..."
  alembic upgrade head || {
    echo "[SAG API] Alembic migration warning: check database connectivity."
  }
  echo "[SAG API] Alembic migrations processed."
else
  echo "[SAG API] WARNING: alembic.ini not found, skipping migration."
fi

echo "[SAG API] Launching SAG API application..."
exec "$@"
