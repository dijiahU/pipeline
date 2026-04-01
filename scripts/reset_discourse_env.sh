#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
source "${ROOT_DIR}/scripts/discourse_env_common.sh"

LAUNCHER_ROOT="${DISCOURSE_LAUNCHER_ROOT:-${ROOT_DIR}/docker/discourse/discourse_docker}"
LAUNCHER_REPO_URL="${DISCOURSE_DOCKER_REPO_URL:-https://github.com/discourse/discourse_docker.git}"
LAUNCHER_REPO_REF="${DISCOURSE_DOCKER_REF:-cfc1ce28054d64d26808545fa5e69660a234c530}"
CONFIG_NAME="${DISCOURSE_CONFIG_NAME:-pipeline-discourse}"
CONTAINER_NAME="${DISCOURSE_CONTAINER_NAME:-${CONFIG_NAME}}"
CONTAINER_CONFIG="${LAUNCHER_ROOT}/containers/${CONFIG_NAME}.yml"
SHARED_DIR="${DISCOURSE_SHARED_DIR:-${ROOT_DIR}/docker/discourse/shared/standalone}"
BASELINE_DIR="${DISCOURSE_BASELINE_DIR:-${ROOT_DIR}/docker/discourse/shared/baseline}"
BASE_URL="${DISCOURSE_BASE_URL:-http://localhost:4200}"
HOSTNAME="${DISCOURSE_HOSTNAME:-localhost:4200}"
PORT_BINDING="${DISCOURSE_PORT_BINDING:-127.0.0.1:4200}"
ENV_FILE="${DISCOURSE_ENV_FILE:-${ROOT_DIR}/.env.discourse.generated}"
MANIFEST_PATH="${DISCOURSE_SEED_MANIFEST:-${ROOT_DIR}/docker/discourse/seed_manifest.json}"
ADMIN_EMAIL="${DISCOURSE_ADMIN_EMAIL:-admin@example.com}"
ADMIN_PASSWORD="${DISCOURSE_ADMIN_PASSWORD:-Admin123!Admin!}"

if [ -f "${ENV_FILE}" ]; then
  set -a
  source "${ENV_FILE}"
  set +a
fi

API_USERNAME="${DISCOURSE_API_USERNAME:-admin}"

ensure_discourse_container_config "${LAUNCHER_ROOT}" "${CONTAINER_CONFIG}" "${SHARED_DIR}" "${HOSTNAME}" "${PORT_BINDING}"

if [ ! -d "${BASELINE_DIR}" ]; then
  bash "${ROOT_DIR}/scripts/setup_discourse_env.sh"
  exit 0
fi

echo "[reset] Destroying existing Discourse container ..."
(cd "${LAUNCHER_ROOT}" && ./launcher destroy "${CONFIG_NAME}" --skip-prereqs) >/dev/null 2>&1 || true
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

restore_discourse_shared_state "${SHARED_DIR}" "${BASELINE_DIR}" reset
rm -f "${LAUNCHER_ROOT}/cids/${CONFIG_NAME}.cid" "${LAUNCHER_ROOT}/cids/${CONFIG_NAME}_bootstrap.cid" >/dev/null 2>&1 || true

echo "[reset] Starting Discourse container ..."
(cd "${LAUNCHER_ROOT}" && ./launcher start "${CONFIG_NAME}" --skip-prereqs)

wait_for_discourse_http "${BASE_URL}" 600 5 reset
write_discourse_env_file "${ENV_FILE}" "${BASE_URL}" "${ADMIN_EMAIL}" "${ADMIN_PASSWORD}" "${DISCOURSE_API_KEY:-}" "${API_USERNAME}" "${CONTAINER_NAME}" "${LAUNCHER_ROOT}" "${CONFIG_NAME}" "${SHARED_DIR}" "${BASELINE_DIR}" "${LAUNCHER_REPO_URL}" "${LAUNCHER_REPO_REF}"

echo "[reset] Discourse reset complete"
