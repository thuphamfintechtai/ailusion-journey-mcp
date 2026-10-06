#!/usr/bin/env bash
# Chạy server MCP (HTTP, cổng theo MCP_PORT trong .env) và MCP Inspector web trong một lệnh.
# Inspector được cấu hình sẵn Streamable HTTP -> http://localhost:<MCP_PORT>/mcp,
# tự mở trình duyệt. Ctrl+C để tắt cả hai.
#
# Chạy từ bất kỳ đâu:  bash scripts_test/dev.sh

set -euo pipefail

cd "$(dirname "$0")/.."
# Lấy cổng giống hệt server: server nạp .env với override=True, nên MCP_PORT trong .env
# thắng biến môi trường. Đọc sai cổng thì script sẽ kill nhầm tiến trình khác (BE chạy ở 8000).
PORT="$(grep -E '^MCP_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d ' "')"
PORT="${PORT:-${MCP_PORT:-8000}}"

if [ -f venv/bin/activate ]; then
  source venv/bin/activate
elif [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
else
  echo "Không thấy venv. Tạo trước: python -m venv venv && source venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

# Dọn port còn kẹt từ lần chạy trước (server, inspector web, sandbox, proxy cũ)
for p in "$PORT" 6274 6275 6277; do
  pids="$(lsof -ti:"$p" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "Port $p đang bị chiếm, tắt tiến trình: $pids"
    kill -9 $pids 2>/dev/null || true
  fi
done

python main.py &
SERVER_PID=$!
trap 'echo; echo "Đang tắt server..."; kill "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM

echo "Đang chờ server lên ở http://localhost:$PORT ..."
for _ in $(seq 1 30); do
  if curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then
    echo "Server đã sẵn sàng: http://localhost:$PORT/mcp"
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "Server không khởi động được, xem log ở trên." >&2
    exit 1
  fi
  sleep 1
done

npx -y @modelcontextprotocol/inspector --web \
  --transport http \
  --server-url "http://localhost:$PORT/mcp"
