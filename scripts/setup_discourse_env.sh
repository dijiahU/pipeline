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

ensure_discourse_launcher_root "${LAUNCHER_ROOT}" "${LAUNCHER_REPO_URL}" "${LAUNCHER_REPO_REF}" setup
ensure_discourse_container_config "${LAUNCHER_ROOT}" "${CONTAINER_CONFIG}" "${SHARED_DIR}" "${HOSTNAME}" "${PORT_BINDING}"

if ! docker image inspect "local_discourse/${CONFIG_NAME}" >/dev/null 2>&1 || [ ! -d "${SHARED_DIR}/postgres_data" ]; then
  echo "[setup] Bootstrapping Discourse image ..."
  rm -rf "${SHARED_DIR}" "${BASELINE_DIR}"
  mkdir -p "${SHARED_DIR}"
  (cd "${LAUNCHER_ROOT}" && ./launcher bootstrap "${CONFIG_NAME}" --skip-prereqs)
fi

echo "[setup] Starting Discourse container ..."
(cd "${LAUNCHER_ROOT}" && ./launcher destroy "${CONFIG_NAME}" --skip-prereqs) >/dev/null 2>&1 || true
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
(cd "${LAUNCHER_ROOT}" && ./launcher start "${CONFIG_NAME}" --skip-prereqs)

wait_for_discourse_http "${BASE_URL}" 600 5 setup
bootstrap_discourse_users "${ROOT_DIR}" "${CONTAINER_NAME}" "${MANIFEST_PATH}" "${ADMIN_EMAIL}" "${ADMIN_PASSWORD}" setup
API_KEY="$(generate_discourse_api_key "${ROOT_DIR}" "${CONTAINER_NAME}" "${ADMIN_EMAIL}")"
if [ -z "${API_KEY}" ]; then
  echo "[setup] Failed to generate Discourse API key"
  exit 1
fi
seed_discourse_data "${ROOT_DIR}" "${BASE_URL}" "${API_KEY}" "${API_USERNAME:-admin}" "${MANIFEST_PATH}" setup
write_discourse_env_file "${ENV_FILE}" "${BASE_URL}" "${ADMIN_EMAIL}" "${ADMIN_PASSWORD}" "${API_KEY}" "${API_USERNAME:-admin}" "${CONTAINER_NAME}" "${LAUNCHER_ROOT}" "${CONFIG_NAME}" "${SHARED_DIR}" "${BASELINE_DIR}" "${LAUNCHER_REPO_URL}" "${LAUNCHER_REPO_REF}"
snapshot_discourse_shared_state "${SHARED_DIR}" "${BASELINE_DIR}" setup

echo "[setup] Discourse is ready"
echo "[setup] Env file written to ${ENV_FILE}"
