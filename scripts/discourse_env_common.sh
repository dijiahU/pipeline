#!/usr/bin/env bash

ensure_discourse_launcher_root() {
  local launcher_root="$1"
  local repo_url="${2:-https://github.com/discourse/discourse_docker.git}"
  local repo_ref="${3:-}"
  local label="${4:-setup}"

  if [ -x "${launcher_root}/launcher" ]; then
    return 0
  fi

  echo "[${label}] Cloning discourse_docker into ${launcher_root} ..."
  rm -rf "${launcher_root}"
  git clone --depth 1 "${repo_url}" "${launcher_root}"
  if [ -n "${repo_ref}" ]; then
    (
      cd "${launcher_root}"
      git fetch --depth 1 origin "${repo_ref}" >/dev/null 2>&1 || true
      git checkout "${repo_ref}" >/dev/null 2>&1 || true
    )
  fi
}

ensure_discourse_container_config() {
  local launcher_root="$1"
  local container_config="$2"
  local shared_dir="$3"
  local hostname="$4"
  local port_binding="$5"

  mkdir -p "$(dirname "${container_config}")"
  cat > "${container_config}" <<EOF
templates:
  - "templates/postgres.template.yml"
  - "templates/redis.template.yml"
  - "templates/web.template.yml"
  - "templates/web.ratelimited.template.yml"

expose:
  - "${port_binding}:80"

params:
  db_default_text_search_config: "pg_catalog.english"

env:
  LC_ALL: en_US.UTF-8
  LANG: en_US.UTF-8
  LANGUAGE: en_US.UTF-8
  UNICORN_WORKERS: 1
  UNICORN_SIDEKIQS: 1
  DISCOURSE_HOSTNAME: "${hostname}"
  DISCOURSE_DEVELOPER_EMAILS: "admin@example.com"
  DISCOURSE_SMTP_ADDRESS: "localhost"
  DISCOURSE_SKIP_EMAIL_SETUP: "1"

volumes:
  - volume:
      host: ${shared_dir}
      guest: /shared
EOF
  chmod 600 "${container_config}"
  mkdir -p "${launcher_root}/shared"
}

wait_for_discourse_http() {
  local base_url="$1"
  local max_wait="${2:-360}"
  local interval="${3:-5}"
  local label="${4:-setup}"
  local elapsed=0

  echo "[${label}] Waiting for Discourse at ${base_url} ..."
  while true; do
    local code
    local body
    body="$(curl -s "${base_url}/about.json" 2>/dev/null || true)"
    code="$(curl -s -o /dev/null -w '%{http_code}' "${base_url}/about.json" 2>/dev/null || echo "000")"
    if [ "${code}" = "200" ] && printf '%s' "${body}" | grep -q '"about"'; then
      echo "[${label}] Discourse is ready (${elapsed}s)"
      return 0
    fi
    if [ "${elapsed}" -ge "${max_wait}" ]; then
      echo "[${label}] Timed out after ${max_wait}s waiting for Discourse"
      return 1
    fi
    sleep "${interval}"
    elapsed=$((elapsed + interval))
    echo "[${label}] Waiting... (${elapsed}s, HTTP ${code})"
  done
}

bootstrap_discourse_users() {
  local root_dir="$1"
  local container_name="$2"
  local manifest_path="$3"
  local admin_email="$4"
  local admin_password="$5"
  local label="${6:-setup}"
  local max_wait="${7:-240}"
  local interval="${8:-5}"
  local elapsed=0

  echo "[${label}] Bootstrapping Discourse users in ${container_name} ..."
  docker cp "${root_dir}/docker/discourse/scripts/bootstrap_discourse.rb" "${container_name}:/tmp/bootstrap_discourse.rb"
  docker cp "${manifest_path}" "${container_name}:/tmp/discourse_seed_manifest.json"
  while true; do
    if docker exec \
      -e DISCOURSE_ADMIN_EMAIL="${admin_email}" \
      -e DISCOURSE_ADMIN_PASSWORD="${admin_password}" \
      "${container_name}" \
      bash -lc "su discourse -c 'cd /var/www/discourse && RAILS_ENV=production bundle exec rails runner /tmp/bootstrap_discourse.rb'" >/dev/null 2>&1; then
      echo "[${label}] Discourse user bootstrap complete"
      return 0
    fi
    if [ "${elapsed}" -ge "${max_wait}" ]; then
      echo "[${label}] Timed out after ${max_wait}s bootstrapping Discourse users"
      return 1
    fi
    sleep "${interval}"
    elapsed=$((elapsed + interval))
    echo "[${label}] Waiting for Discourse rails bootstrap... (${elapsed}s)"
  done
}

generate_discourse_api_key() {
  local root_dir="$1"
  local container_name="$2"
  local admin_email="$3"
  local max_wait="${4:-180}"
  local interval="${5:-5}"
  local elapsed=0
  while true; do
    local output
    output="$(docker exec \
      -e DISCOURSE_ADMIN_EMAIL="${admin_email}" \
      "${container_name}" \
      bash -lc "su discourse -c 'cd /var/www/discourse && RAILS_ENV=production bundle exec rake api_key:create_master[PipelineMasterKey]'" 2>/dev/null | tail -n 1 | tr -d '\r' || true)"
    if [ -n "${output}" ]; then
      printf '%s\n' "${output}"
      return 0
    fi
    if [ "${elapsed}" -ge "${max_wait}" ]; then
      return 1
    fi
    sleep "${interval}"
    elapsed=$((elapsed + interval))
  done
}

write_discourse_env_file() {
  local env_file="$1"
  local base_url="$2"
  local admin_email="$3"
  local admin_password="$4"
  local api_key="$5"
  local api_username="$6"
  local container_name="$7"
  local launcher_root="$8"
  local config_name="$9"
  local shared_dir="${10}"
  local baseline_dir="${11}"
  local repo_url="${12}"
  local repo_ref="${13}"

  cat > "${env_file}" <<EOF
DISCOURSE_BASE_URL=${base_url}
DISCOURSE_ADMIN_EMAIL=${admin_email}
DISCOURSE_ADMIN_PASSWORD=${admin_password}
DISCOURSE_API_KEY=${api_key}
DISCOURSE_API_USERNAME=${api_username}
DISCOURSE_CONTAINER_NAME=${container_name}
DISCOURSE_LAUNCHER_ROOT=${launcher_root}
DISCOURSE_CONFIG_NAME=${config_name}
DISCOURSE_SHARED_DIR=${shared_dir}
DISCOURSE_BASELINE_DIR=${baseline_dir}
DISCOURSE_DOCKER_REPO_URL=${repo_url}
DISCOURSE_DOCKER_REF=${repo_ref}
EOF
}

seed_discourse_data() {
  local root_dir="$1"
  local base_url="$2"
  local api_key="$3"
  local api_username="$4"
  local manifest_path="$5"
  local label="${6:-seed}"

  echo "[${label}] Seeding Discourse data from ${manifest_path} ..."
  DISCOURSE_BASE_URL="${base_url}" \
  DISCOURSE_API_KEY="${api_key}" \
  DISCOURSE_API_USERNAME="${api_username}" \
  DISCOURSE_SEED_MANIFEST="${manifest_path}" \
  python3 "${root_dir}/docker/discourse/scripts/seed_discourse_data.py"
}

snapshot_discourse_shared_state() {
  local shared_dir="$1"
  local baseline_dir="$2"
  local label="${3:-setup}"

  echo "[${label}] Saving Discourse baseline to ${baseline_dir} ..."
  rm -rf "${baseline_dir}"
  mkdir -p "$(dirname "${baseline_dir}")"
  cp -a "${shared_dir}" "${baseline_dir}"
}

restore_discourse_shared_state() {
  local shared_dir="$1"
  local baseline_dir="$2"
  local label="${3:-reset}"

  if [ ! -d "${baseline_dir}" ]; then
    echo "[${label}] Missing baseline directory: ${baseline_dir}"
    return 1
  fi
  echo "[${label}] Restoring Discourse baseline from ${baseline_dir} ..."
  rm -rf "${shared_dir}"
  cp -a "${baseline_dir}" "${shared_dir}"
}
