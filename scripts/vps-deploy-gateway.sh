#!/usr/bin/env bash
set -Eeuo pipefail

read -r release_sha || { echo "Missing release SHA" >&2; exit 2; }
read -r ghcr_token || { echo "Missing registry token" >&2; exit 2; }
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid release SHA" >&2; exit 2; }
[[ ${#ghcr_token} -ge 20 && "$ghcr_token" != *[[:space:]]* ]] || {
  echo "Invalid registry token" >&2
  exit 2
}

app_dir=/opt/aiinvest
release_dir="/opt/aiinvest-deploy/releases/$release_sha"
mkdir -p "$(dirname "$release_dir")"

git -C "$app_dir" fetch --no-tags origin "$release_sha"
if [[ ! -d "$release_dir" ]]; then
  git -C "$app_dir" worktree add --detach "$release_dir" "$release_sha"
elif [[ "$(git -C "$release_dir" rev-parse HEAD)" != "$release_sha" ]]; then
  echo "Release worktree points at a different commit" >&2
  exit 2
fi

trap 'docker logout ghcr.io >/dev/null 2>&1 || true; unset ghcr_token' EXIT
printf '%s' "$ghcr_token" | docker login ghcr.io --username thanhtai040805 --password-stdin >/dev/null
unset ghcr_token
bash "$release_dir/scripts/deploy-vps.sh" "$release_sha"
