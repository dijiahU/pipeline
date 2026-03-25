#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
source "${ROOT_DIR}/scripts/gitea_env_common.sh"

TEMP_CONTAINER="${GITEA_BUILD_CONTAINER_NAME:-pipeline-gitea-build}"
BASE_IMAGE="${GITEA_BASE_IMAGE:-gitea/gitea:1.22}"
TARGET_IMAGE="${GITEA_TARGET_IMAGE:-pipeline-gitea:latest}"
TEMP_PORT="${GITEA_BUILD_HTTP_PORT:-3300}"
TEMP_BASE_URL="http://localhost:${TEMP_PORT}"
ADMIN_USERNAME="${GITEA_ADMIN_USERNAME:-root}"
ADMIN_PASSWORD="${GITEA_ADMIN_PASSWORD:-root123456}"
ADMIN_EMAIL="${GITEA_ADMIN_EMAIL:-root@example.com}"
OWNER="${GITEA_OWNER:-${ADMIN_USERNAME}}"
ENV_FILE="${GITEA_ENV_FILE:-${ROOT_DIR}/.env.gitea.generated}"
TOKEN_NAME="${GITEA_TOKEN_NAME:-pipeline-build-$(date +%s)}"
TOKEN_SCOPES="${GITEA_TOKEN_SCOPES:-all}"

cleanup() {
  docker rm -f "${TEMP_CONTAINER}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[build] Starting temporary Gitea container ${TEMP_CONTAINER} ..."
cleanup
docker run -d \
  --name "${TEMP_CONTAINER}" \
  -p "${TEMP_PORT}:3000" \
  -e GITEA__database__DB_TYPE=sqlite3 \
  -e GITEA__security__INSTALL_LOCK=true \
  -e GITEA__service__DISABLE_REGISTRATION=true \
  -e GITEA__actions__ENABLED=true \
  -e GITEA__server__ROOT_URL="${TEMP_BASE_URL}" \
  "${BASE_IMAGE}" >/dev/null

wait_for_gitea_api "${TEMP_BASE_URL}" 120 2 build
ensure_gitea_admin_user "${TEMP_CONTAINER}" "${ADMIN_USERNAME}" "${ADMIN_PASSWORD}" "${ADMIN_EMAIL}" build

echo "[build] Creating seed token ..."
TOKEN="$(create_gitea_access_token "${TEMP_BASE_URL}" "${ADMIN_USERNAME}" "${ADMIN_PASSWORD}" "${TOKEN_NAME}" "${TOKEN_SCOPES}")"
VERIFY_CODE="$(verify_gitea_token "${TEMP_BASE_URL}" "${TOKEN}")"
if [ "${VERIFY_CODE}" != "200" ]; then
  echo "[build] Token verification failed with HTTP ${VERIFY_CODE}"
  exit 1
fi

echo "[build] Seeding repositories ..."
GITEA_BASE_URL="${TEMP_BASE_URL}" \
GITEA_ACCESS_TOKEN="${TOKEN}" \
GITEA_OWNER="${OWNER}" \
python3 "${ROOT_DIR}/docker/gitea/scripts/seed_gitea_data.py"

echo "[build] Committing ${TEMP_CONTAINER} to ${TARGET_IMAGE} ..."
docker commit "${TEMP_CONTAINER}" "${TARGET_IMAGE}" >/dev/null

write_gitea_env_file "${ENV_FILE}" "http://localhost:3000" "${TOKEN}" "${OWNER}" "pipeline-gitea" "${SANDBOX_MODE:-preview}"

echo "[build] Image built: ${TARGET_IMAGE}"
echo "[build] Runtime env written to ${ENV_FILE}"
echo "[build] Run with: GITEA_IMAGE=${TARGET_IMAGE} docker compose -f docker-compose.yml up -d"
