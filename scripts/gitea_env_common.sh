#!/usr/bin/env bash

wait_for_gitea_api() {
  local base_url="$1"
  local max_wait="${2:-120}"
  local interval="${3:-2}"
  local label="${4:-setup}"
  local elapsed=0

  echo "[${label}] Waiting for Gitea API at ${base_url} ..."
  while true; do
    local status
    status=$(curl -s -o /dev/null -w '%{http_code}' "${base_url}/api/v1/version" 2>/dev/null || echo "000")
    if [ "${status}" = "200" ]; then
      echo "[${label}] Gitea API is ready (${elapsed}s)"
      return 0
    fi
    if [ "${elapsed}" -ge "${max_wait}" ]; then
      echo "[${label}] Timed out after ${max_wait}s waiting for Gitea API"
      return 1
    fi
    sleep "${interval}"
    elapsed=$((elapsed + interval))
    echo "[${label}] Waiting... (${elapsed}s, HTTP ${status})"
  done
}

resolve_repo_path() {
  local root_dir="$1"
  local target_path="$2"
  if [[ "${target_path}" = /* ]]; then
    printf '%s\n' "${target_path}"
  else
    printf '%s\n' "${root_dir}/${target_path}"
  fi
}

ensure_gitea_admin_user() {
  local container_name="$1"
  local username="$2"
  local password="$3"
  local email="$4"
  local label="${5:-setup}"

  echo "[${label}] Ensuring admin user ${username} exists ..."
  if docker exec --user git "${container_name}" gitea admin user create \
    --admin \
    --username "${username}" \
    --password "${password}" \
    --email "${email}" \
    --must-change-password=false >/tmp/gitea-admin-create.log 2>&1; then
    echo "[${label}] Admin user ${username} created"
    return 0
  fi

  if grep -qi "already exists" /tmp/gitea-admin-create.log; then
    echo "[${label}] Admin user ${username} already exists"
    return 0
  fi

  cat /tmp/gitea-admin-create.log
  return 1
}

create_gitea_access_token() {
  local base_url="$1"
  local username="$2"
  local password="$3"
  local token_name="$4"
  local token_scopes="${5:-all}"

  local response_file
  response_file=$(mktemp)
  local http_code
  local payload
  payload=$(python3 - "${token_name}" "${token_scopes}" <<'PY'
import json
import sys

name = sys.argv[1]
scopes = [part.strip() for part in sys.argv[2].split(",") if part.strip()]
print(json.dumps({"name": name, "scopes": scopes}, ensure_ascii=False))
PY
  )
  http_code=$(curl -sS -u "${username}:${password}" \
    -H "Content-Type: application/json" \
    -o "${response_file}" \
    -w '%{http_code}' \
    -X POST "${base_url}/api/v1/users/${username}/tokens" \
    -d "${payload}")

  if [ "${http_code}" != "201" ]; then
    echo "[token] Failed to create token (${http_code})" >&2
    cat "${response_file}" >&2
    rm -f "${response_file}"
    return 1
  fi

  python3 - "${response_file}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    data = json.load(fh)
token = data.get("sha1") or data.get("token") or ""
if not token:
    raise SystemExit("missing token value in Gitea response")
print(token)
PY
  rm -f "${response_file}"
}

verify_gitea_token() {
  local base_url="$1"
  local token="$2"
  curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: token ${token}" \
    "${base_url}/api/v1/user"
}

write_gitea_env_file() {
  local env_file="$1"
  local base_url="$2"
  local token="$3"
  local owner="$4"
  local container_name="$5"
  local sandbox_mode="${6:-preview}"

  cat > "${env_file}" <<EOF
GITEA_BASE_URL=${base_url}
GITEA_ACCESS_TOKEN=${token}
GITEA_OWNER=${owner}
PIPELINE_ENV=gitea
SANDBOX_MODE=${sandbox_mode}
SANDBOX_CONTAINER_NAME=${container_name}
EOF
}

seed_gitea_data() {
  local root_dir="$1"
  local base_url="$2"
  local token="$3"
  local owner="$4"
  local manifest_path="${5:-${root_dir}/docker/gitea/seed_manifest.json}"
  local label="${6:-seed}"

  echo "[${label}] Seeding Gitea data from ${manifest_path} ..."
  GITEA_BASE_URL="${base_url}" \
  GITEA_ACCESS_TOKEN="${token}" \
  GITEA_OWNER="${owner}" \
  GITEA_SEED_MANIFEST="${manifest_path}" \
  python3 "${root_dir}/docker/gitea/scripts/seed_gitea_data.py"
}
