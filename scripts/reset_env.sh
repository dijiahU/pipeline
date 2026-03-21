#!/usr/bin/env bash
set -euo pipefail

GITLAB_URL="${GITLAB_BASE_URL:-http://localhost:8929}"
GITLAB_TOKEN="${GITLAB_ACCESS_TOKEN:-root-token}"
MAX_WAIT=600   # amd64 模拟下 GitLab 启动较慢，最多等 10 分钟
INTERVAL=5

echo "[reset] 停止并删除 GitLab 容器..."
docker compose down

echo "[reset] 从镜像重建容器（恢复初始数据）..."
docker compose up -d

echo "[reset] 等待 GitLab 启动..."
elapsed=0
while true; do
    # 先检查容器是否还活着
    container_status=$(docker compose ps --format '{{.Status}}' 2>/dev/null | head -1)
    if echo "$container_status" | grep -qi "exited\|dead\|restarting"; then
        echo "[reset] 容器异常: $container_status"
        echo "[reset] 查看日志: docker logs pipeline-gitlab-1"
        exit 1
    fi

    # 检查 API（此时 token 可能过期，接受 401 表示 GitLab 已启动）
    http_code=$(curl -s -o /dev/null -w '%{http_code}' "$GITLAB_URL/api/v4/projects" \
        -H "PRIVATE-TOKEN: $GITLAB_TOKEN" 2>/dev/null || echo "000")

    if [ "$http_code" = "200" ]; then
        echo "[reset] GitLab API 可用 (${elapsed}s)"
        break
    fi

    if [ "$http_code" = "401" ]; then
        echo "[reset] GitLab 已启动，token 需要续期 (${elapsed}s)"
        break
    fi

    if [ "$elapsed" -ge "$MAX_WAIT" ]; then
        echo "[reset] 超时：GitLab 在 ${MAX_WAIT}s 内未就绪"
        exit 1
    fi

    sleep "$INTERVAL"
    elapsed=$((elapsed + INTERVAL))
    echo "[reset] 等待中... (${elapsed}s, HTTP $http_code)"
done

echo "[reset] 续期 access token..."
docker exec pipeline-gitlab-1 gitlab-rails runner "
token = PersonalAccessToken.find_by(name: 'root-token')
if token
  token.update_column(:expires_at, 1.year.from_now)
  puts 'renewed'
else
  user = User.find_by(username: 'root')
  t = user.personal_access_tokens.new(name: 'root-token', scopes: [:api, :read_user, :read_repository, :write_repository, :sudo], expires_at: 1.year.from_now)
  t.set_token('root-token')
  t.save(validate: false)
  puts 'created'
end
"

echo "[reset] 验证 API..."
verify=$(curl -s -o /dev/null -w '%{http_code}' "$GITLAB_URL/api/v4/projects" \
    -H "PRIVATE-TOKEN: $GITLAB_TOKEN" 2>/dev/null || echo "000")

if [ "$verify" = "200" ]; then
    echo "[reset] 环境已重置并就绪"
else
    echo "[reset] 警告：API 返回 HTTP $verify，可能需要再等一会儿"
    exit 1
fi
