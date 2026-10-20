# Contributing

Thanks for helping. Bug reports, fixes and docs are all welcome.

## Setup

```bash
git clone https://github.com/EgiStr/agent-headless-browser && cd agent-headless-browser
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

## Before you open a PR

```bash
ruff check . && ruff format --check .
mypy
pytest                               # unit tests, no browser
AHB_E2E=1 pytest -m integration      # real Chrome; no network or Google account needed
```

CI runs the same checks on Linux and Windows for Python 3.10 to 3.13.

## Guidelines

- **Keep a test with each change.** Pure logic (request building, decoding, argument building) is unit tested. Behaviour
  that needs Chrome goes in `tests/integration`, using local `data:` pages, not live sites.
- **Never commit a profile, cookies, exports, or a real report ID or email**, including in tests, docs and issue text.
- **Follow the session rules in [docs/findings.md](docs/findings.md):** one Chrome per profile, graceful stop, no cookie
  transplants. A change that breaks these will revoke users' Google sessions.
- **Library code logs to stderr through `logging`.** Stdout belongs to command results and the MCP stdio channel.
- **Write commit messages in the imperative.** Add a line under `Unreleased` in [CHANGELOG.md](CHANGELOG.md) for
  anything a user would notice.

## Reporting bugs

Use the issue form. Include `ahb --version`, your OS, `google-chrome --version`, and the failing command. Redact emails
and report IDs.
