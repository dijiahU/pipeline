#!/usr/bin/env bash

wait_for_owncloud_api() {
  local base_url="$1"
  local max_wait="${2:-120}"
  local interval="${3:-3}"
  local label="${4:-setup}"
  local elapsed=0

  echo "[${label}] Waiting for ownCloud oCIS at ${base_url} ..."
  while true; do
    local code
    code=$(curl -sk -o /dev/null -w '%{http_code}' "${base_url}/.well-known/openid-configuration" 2>/dev/null || echo "000")
    if [ "${code}" = "200" ]; then
      echo "[${label}] ownCloud oCIS is ready (${elapsed}s)"
      return 0
    fi
    if [ "${elapsed}" -ge "${max_wait}" ]; then
      echo "[${label}] Timed out after ${max_wait}s waiting for ownCloud"
      return 1
    fi
    sleep "${interval}"
    elapsed=$((elapsed + interval))
    echo "[${label}] Waiting... (${elapsed}s, HTTP ${code})"
  done
}

write_owncloud_env_file() {
  local env_file="$1"
  local base_url="$2"
  local admin_user="$3"
  local admin_password="$4"
  local container_name="$5"

  cat > "${env_file}" <<EOF
OWNCLOUD_BASE_URL=${base_url}
OWNCLOUD_ADMIN_USER=${admin_user}
OWNCLOUD_ADMIN_PASSWORD=${admin_password}
OWNCLOUD_CONTAINER_NAME=${container_name}
EOF
}

seed_owncloud_data() {
  local root_dir="$1"
  local base_url="$2"
  local admin_user="$3"
  local admin_password="$4"
  local manifest_path="${5:-${root_dir}/docker/owncloud/seed_manifest.json}"
  local label="${6:-seed}"

  echo "[${label}] Seeding ownCloud data from ${manifest_path} ..."
  OWNCLOUD_BASE_URL="${base_url}" \
  OWNCLOUD_ADMIN_USER="${admin_user}" \
  OWNCLOUD_ADMIN_PASSWORD="${admin_password}" \
  OWNCLOUD_SEED_MANIFEST="${manifest_path}" \
  python3 "${root_dir}/docker/owncloud/scripts/seed_owncloud_data.py"
}
