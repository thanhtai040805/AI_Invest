#!/usr/bin/env bash
set -Eeuo pipefail

release_sha="${1:?commit SHA is required}"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid release SHA" >&2; exit 2; }

app_dir=/opt/aiinvest
deploy_dir=/opt/aiinvest-deploy
release_dir="$deploy_dir/releases/$release_sha"
backup_dir=/opt/aiinvest-backups
state_dir="$deploy_dir/state"
compose_files=(
  --project-name aiinvest
  --project-directory "$app_dir"
  --env-file "$app_dir/.env"
  -f "$release_dir/docker-compose.yml"
  -f "$release_dir/docker-compose.prod-vps.yml"
)

compose() {
  AIINVEST_IMAGE_TAG="$release_sha" docker compose "${compose_files[@]}" "$@"
}

[[ -f "$app_dir/.env" ]] || { echo "VPS .env is missing" >&2; exit 2; }
[[ -f "$app_dir/nginx/certs/fullchain.pem" && -f "$app_dir/nginx/certs/privkey.pem" ]] || {
  echo "TLS certificate files are missing" >&2
  exit 2
}

mkdir -p "$state_dir"
exec 9>/run/lock/aiinvest-deploy.lock
flock -n 9 || { echo "Another AIInvest deployment is running" >&2; exit 2; }

mkdir -p "$deploy_dir/releases"
git -C "$app_dir" fetch --no-tags origin "$release_sha"
if [[ ! -d "$release_dir" ]]; then
  git -C "$app_dir" worktree add --detach "$release_dir" "$release_sha"
elif [[ "$(git -C "$release_dir" rev-parse HEAD)" != "$release_sha" ]]; then
  echo "Release path exists with a different commit" >&2
  exit 2
fi

compose config -q

# Back up only when this release introduces an unapplied Prisma migration.
applied_migrations="$(compose exec -T postgres psql -U postgres -d aiinvest -Atqc \
  'SELECT migration_name FROM _prisma_migrations WHERE finished_at IS NOT NULL AND rolled_back_at IS NULL')"
pending_migrations=()
for migration in "$release_dir"/back-end/prisma/migrations/*/; do
  [[ -d "$migration" ]] || continue
  migration_name="$(basename "$migration")"
  grep -Fxq "$migration_name" <<<"$applied_migrations" || pending_migrations+=("$migration_name")
done

if ((${#pending_migrations[@]})); then
  db_bytes="$(compose exec -T postgres psql -U postgres -d aiinvest -Atqc \
    "SELECT pg_database_size('aiinvest')")"
  free_bytes="$(df --output=avail -B1 "$app_dir" | tail -n 1 | tr -d ' ')"
  required_bytes=$((db_bytes + 1073741824))
  if ((free_bytes < required_bytes)); then
    echo "Not enough free disk space for a pre-migration backup; refusing deployment" >&2
    exit 3
  fi

  mkdir -p "$backup_dir"
  backup_tmp="$backup_dir/aiinvest-$release_sha.dump.partial"
  backup_file="$backup_dir/aiinvest-$release_sha.dump"
  trap 'rm -f "${backup_tmp:-}"' EXIT
  echo "Pending migrations: ${pending_migrations[*]}"
  echo "Creating a compressed PostgreSQL backup before migration"
  compose exec -T postgres pg_dump -U postgres -d aiinvest -Fc -Z 3 >"$backup_tmp"
  [[ -s "$backup_tmp" ]]
  compose exec -T postgres pg_restore --list <"$backup_tmp" >/dev/null
  mv "$backup_tmp" "$backup_file"
  echo "Verified backup saved at $backup_file"
  trap - EXIT
fi

compose pull migrate ai-engine backend frontend nginx
compose up -d --no-build --pull never --wait --wait-timeout 240
curl --retry 12 --retry-delay 5 --retry-connrefused --max-time 15 -fsSI https://aiinvest.cloud/ >/dev/null
curl --retry 12 --retry-delay 5 --retry-connrefused --max-time 15 -fsS https://aiinvest.cloud/api/health >/dev/null

deployed_sha=""
if [[ -f "$state_dir/current" ]]; then
  deployed_sha="$(<"$state_dir/current")"
  [[ "$deployed_sha" =~ ^[0-9a-f]{40}$ ]] || deployed_sha=""
fi
printf '%s\n' "$release_sha" >"$state_dir/current"
chmod 600 "$state_dir/current"

# Keep the deployed release and the immediately preceding deployed release.
previous_sha=""
if [[ -f "$state_dir/previous" ]]; then
  previous_sha="$(<"$state_dir/previous")"
  [[ "$previous_sha" =~ ^[0-9a-f]{40}$ && "$previous_sha" != "$release_sha" ]] || previous_sha=""
fi
if [[ -n "${deployed_sha:-}" && "$deployed_sha" != "$release_sha" ]]; then
  previous_sha="$deployed_sha"
fi
if [[ -n "$previous_sha" ]]; then
  printf '%s\n' "$previous_sha" >"$state_dir/previous"
  chmod 600 "$state_dir/previous"
else
  rm -f "$state_dir/previous"
fi

app_repositories=(
  ghcr.io/thanhtai040805/aiinvest-backend
  ghcr.io/thanhtai040805/aiinvest-ai-engine
  ghcr.io/thanhtai040805/aiinvest-frontend
  ghcr.io/thanhtai040805/aiinvest-nginx
  aiinvest-backend
  aiinvest-ai-engine
  aiinvest-frontend
  aiinvest-nginx
)
stale_images=()
while IFS= read -r image_ref; do
  repository="${image_ref%:*}"
  tag="${image_ref##*:}"
  for app_repository in "${app_repositories[@]}"; do
    [[ "$repository" == "$app_repository" ]] || continue
    if [[ "$tag" != "$release_sha" && "$tag" != "$previous_sha" ]]; then
      stale_images+=("$image_ref")
    fi
    break
  done
done < <(docker image ls --format '{{.Repository}}:{{.Tag}}')

if ((${#stale_images[@]})); then
  echo "Removing obsolete AIInvest images: ${stale_images[*]}"
  docker image rm "${stale_images[@]}" || echo "Warning: one or more obsolete images could not be removed" >&2
fi

echo "Pruning unused Docker build cache"
docker builder prune --all --force || echo "Warning: Docker build cache pruning failed" >&2

compose ps
echo "Deployment $release_sha is healthy"
