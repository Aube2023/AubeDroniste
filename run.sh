#!/usr/bin/env bash
# Demarrage AubeDroniste - dev local.
set -euo pipefail

cd "$(dirname "$0")"

VENV_DIR="${VENV_DIR:-.venv}"
PORT="${PORT:-5034}"

if [ ! -d "$VENV_DIR" ]; then
  echo "[aubedroniste] creation venv..."
  python3 -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
pip install -q --upgrade pip
pip install -q -r requirements.txt

if [ ! -f "data/aubedroniste.db" ]; then
  echo "[aubedroniste] init DB + seed demo..."
  python3 -c "import db; db.init_schema('schema.sql')"
  python3 scripts/seed.py || true
fi

echo "[aubedroniste] http://127.0.0.1:${PORT}"
exec python3 app.py
