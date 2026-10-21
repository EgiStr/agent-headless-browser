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

# One downloader serves every fetch, so a machine that only ships wget still
# gets uv after install.py was fetched the same way.
downloader=""
pick_downloader() {
  if command -v curl >/dev/null 2>&1; then
    downloader=curl
  elif command -v wget >/dev/null 2>&1; then
    downloader=wget
  else
    echo "error: neither curl nor wget is available, nothing can be downloaded" >&2
    return 1
  fi
}

fetch_to_file() {
  # $1 = url, $2 = destination file
  if [ "$downloader" = curl ]; then
    curl -fsSL "$1" -o "$2"
  else
    wget -qO "$2" "$1"
  fi
}

fetch_to_stdout() {
  # $1 = url
  if [ "$downloader" = curl ]; then
    curl -LsSf "$1"
  else
    wget -qO- "$1"
  fi
}

# Reuse a neighbouring install.py only when this script really is a file on
# disk. When it is piped in (curl ... | sh) $0 is the shell name, and the
# current directory may hold an unrelated install.py that must not be run.
self=$0
case ${self##*/} in
  sh|bash|dash|zsh|ksh) self="" ;;
esac

installer=""
if [ -n "$self" ] && [ -f "$self" ]; then
  here=$(CDPATH= cd -- "$(dirname -- "$self")" 2>/dev/null && pwd || echo .)
  if [ -f "$here/install.py" ]; then
    installer="$here/install.py"
  fi
fi

if [ -z "$installer" ]; then
  pick_downloader || exit 1
  tmp=$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/ahb-install-$$")
  mkdir -p "$tmp"
  installer="$tmp/install.py"
  echo "==> downloading install.py"
  if ! fetch_to_file "$BASE/install.py" "$installer"; then
    echo "error: could not download install.py from $BASE" >&2
    exit 1
  fi
fi

python_ok() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

# A dry run must not change the machine, so it must not install uv either.
dry_run=0
for arg in "$@"; do
  case $arg in
    --dry-run) dry_run=1 ;;
  esac
done

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
    exec uv run --no-project --python 3.12 "$installer" ${@+"$@"}
  fi
  if [ "$dry_run" -eq 1 ]; then
    echo "==> would install uv (it can provide Python 3.12); a dry run changes nothing"
    exit 0
  fi
  pick_downloader || exit 1
  echo "==> installing uv (it can provide Python 3.12)"
  fetch_to_stdout "https://astral.sh/uv/install.sh" | sh
  PATH="$HOME/.local/bin:$PATH"
  export PATH
  if command -v uv >/dev/null 2>&1; then
    exec uv run --no-project --python 3.12 "$installer" ${@+"$@"}
  fi
  echo "error: no Python 3.10+ found and uv could not be installed" >&2
  exit 1
fi

exec "$PY" "$installer" ${@+"$@"}
