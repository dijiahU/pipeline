#!/usr/bin/env bash
set -euo pipefail

GITLAB_URL="${GITLAB_BASE_URL:-http://localhost:8929}"
MAX_WAIT=300   # 最多等 5 分钟
INTERVAL=5

echo "[setup] 启动 GitLab 容器..."
docker compose up -d

echo "[setup] 等待 GitLab API 就绪 ($GITLAB_URL) ..."
elapsed=0
while true; do
    status=$(curl -s -o /dev/null -w '%{http_code}' "$GITLAB_URL/api/v4/projects" \
        -H "PRIVATE-TOKEN: ${GITLAB_ACCESS_TOKEN:-root-token}" 2>/dev/null || echo "000")
    if [ "$status" = "200" ]; then
        echo "[setup] GitLab API 可用 (${elapsed}s)"
        break
    fi
    if [ "$elapsed" -ge "$MAX_WAIT" ]; then
        echo "[setup] 超时：GitLab API 在 ${MAX_WAIT}s 内未就绪"
        exit 1
    fi
    sleep "$INTERVAL"
    elapsed=$((elapsed + INTERVAL))
    echo "[setup] 等待中... (${elapsed}s, HTTP $status)"
done

echo "[setup] 环境就绪"
