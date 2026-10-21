#!/bin/sh
# One-click installer for agent-headless-browser (macOS and Linux).
#
#   curl -fsSL https://raw.githubusercontent.com/EgiStr/agent-headless-browser/main/scripts/install.sh | sh
#
# It finds a Python 3.10+, downloads install.py when it is not next to this
# script, and runs it. With no Python at all it bootstraps uv, which can
# provide one. Any argument is passed straight to install.py.
set -eu

BASE="${AHB_INSTALL_BASE_URL:-https://raw.githubusercontent.com/EgiStr/agent-headless-browser/main/scripts}"
here=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || echo .)

installer="$here/install.py"
if [ ! -f "$installer" ]; then
  tmp=$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/ahb-install-$$")
  mkdir -p "$tmp"
  installer="$tmp/install.py"
  echo "==> downloading install.py"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$BASE/install.py" -o "$installer"
  else
    wget -qO "$installer" "$BASE/install.py"
  fi
fi

python_ok() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then
    PY="$candidate"
    break
  fi
done

if [ -z "$PY" ]; then
  if command -v uv >/dev/null 2>&1; then
    echo "==> no Python 3.10+ found, using uv to provide one"
    exec uv run --no-project --python 3.12 "$installer" "$@"
  fi
  echo "==> installing uv (it can provide Python 3.12)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
  export PATH
  if command -v uv >/dev/null 2>&1; then
    exec uv run --no-project --python 3.12 "$installer" "$@"
  fi
  echo "error: no Python 3.10+ found and uv could not be installed" >&2
  exit 1
fi

exec "$PY" "$installer" "$@"
