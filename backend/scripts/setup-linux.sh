#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/.."
command -v python3 >/dev/null || { echo 'Python 3 não encontrado.' >&2; exit 1; }
command -v npm >/dev/null || { echo 'Node.js e npm são necessários.' >&2; exit 1; }
if [ ! -f .env ]; then cp .env.example .env; fi
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
if [ -f frontend/package-lock.json ]; then (cd frontend && npm ci); else (cd frontend && npm install); fi
printf '%s\n' 'Ada preparada. Configure .env e execute: make dev'
