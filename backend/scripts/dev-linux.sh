#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/../.."
backend_pid=''
frontend_pid=''
cleanup() {
  if [ -n "$backend_pid" ]; then kill -- "-$backend_pid" 2>/dev/null || true; fi
  if [ -n "$frontend_pid" ]; then kill -- "-$frontend_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM
setsid bash -c 'cd backend; exec ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000' &
backend_pid=$!
setsid bash -c 'cd frontend; exec npm run dev' &
frontend_pid=$!
wait -n "$backend_pid" "$frontend_pid"
