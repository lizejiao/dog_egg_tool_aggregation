#!/usr/bin/env bash
# listing-guard Web 服务启动脚本
# 用法：./scripts/serve.sh [端口]   （默认 8300）
set -e
cd "$(dirname "$0")/.."
PORT="${1:-8300}"
echo "listing-guard Web 控制台启动中 → http://127.0.0.1:${PORT}"
exec uv run uvicorn listing_guard.api.main:app --host 127.0.0.1 --port "$PORT"
