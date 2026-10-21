# Development

```bash
pip install -e ".[dev]"
pytest                              # unit tests, no browser, no network
AHB_E2E=1 pytest -m integration     # real Chrome, local pages, no Google account
ruff check . && ruff format --check . && mypy
```

Python 3.10 – 3.13. The package is `src/`-layout and built with hatchling.

## What CI runs

| Job | What it does |
|---|---|
| `lint` | `ruff check`, `ruff format --check`, `mypy` (strict) |
| `test` | `pytest --cov` on ubuntu and windows × Python 3.10, 3.11, 3.12, 3.13 |
| `integration` | `pytest -m integration` with `AHB_E2E=1`: real Chrome on the runner, local pages only |
| `package` | `python -m build`, `twine check dist/*`, then install the wheel in a fresh venv and run `ahb --version` |

The integration test deliberately needs **no Google account**: it drives a local page. Everything that needs the real
session is a manual check, because CI cannot sign in.

## Test layout

| File | Covers |
|---|---|
| `tests/test_chrome.py` | Chrome discovery, launch flags, the user agent |
| `tests/test_install.py` | The installer: DSH patch shapes, Claude Code registration, the generic snippet, backups, dry run |
| `tests/test_looker.py` | The XSSI prefix, the column decoder, count expansion, the request builder |
| `tests/test_interfaces.py` | The CLI: argument parsing, the start modes, `ahb status` when nothing runs, error reporting |
| `tests/test_small_modules.py` | `config` (settings from the environment), `google` (the account list), `health` (the check and the log) |
| `tests/test_mcp_example.py` | `mcp.example.json` matches the settings the code reads |
| `tests/integration/test_e2e.py` | Real Chrome: start, navigate, snapshot, click, stop |

### Writing a test that does not depend on the machine

The unit tests must pass on a runner that has Chrome installed **and** on one that does not. Three traps, all hit here:

```python
# 1. The fixed paths are real on a Linux runner. Neutralise them when the
#    test is about PATH.
monkeypatch.setattr(chrome, "LINUX_CHROME_PATHS", (Path("/nonexistent/google-chrome"),))

# 2. monkeypatch.setenv("PATH", value, prepend=os.pathsep) PREPENDS. It keeps the
#    real PATH, so a real `ahb-mcp` on the machine can answer first.
monkeypatch.setenv("PATH", str(tmp_path))  # replace, do not prepend

# 3. shutil.which needs the exec bit on POSIX.
script.chmod(0o755)
```

`scripts/install.py` and `src/agent_headless_browser/chrome.py` must agree on where Chrome lives. A test asserts they do;
change one, change the other, or the test fails.

## Adding a tool

1. Write the function in `src/agent_headless_browser/mcp_server.py` under `@tool`, with a docstring — mcp shows it to the
   model as the tool description.
2. Keep the return value JSON-serialisable. Failures must raise; the `@tool` wrapper turns them into `ToolError`.
3. Add a row to the table in `docs/mcp-tools.md` and to the tool table in `.claude/skills/agent-browser/SKILL.md`.
4. Add a test in `tests/test_interfaces.py` or the module's own test file.
5. Add a line to `CHANGELOG.md` under `### Added`.

## Conventions

- **Types everywhere.** `mypy --strict` is clean; keep it that way.
- **`scripts/install.py` uses the standard library only.** It has to run before anything is installed, so it cannot import
  the package.
- **Never commit** a profile, a cookie, an export, a report id or a real email address. `out/`, `*.csv`, `.mcp.json`,
  `dist/` and `*.egg-info/` are gitignored.
- **Stage explicit paths**, never a glob: `git add -- src/agent_headless_browser/chrome.py`.
- **Commit messages are imperative**: "Add the one-click installer", not "Added …".
- **User-visible changes get a CHANGELOG entry** under `### Added`, `### Fixed`, `### Changed` or `### Removed`.

## Pre-commit

`.pre-commit-config.yaml` is wired up, but its pinned ruff (`v0.6.9`) does not know the `ASYNC240` rule this repo's own
config uses, and its mypy hook runs with `language: system` against a broken system mypy. **CI does not run pre-commit**, so
this does not block a merge. Run `ruff`/`mypy`/`pytest` from the virtualenv instead, and fix the pins in a separate change
if you want the hooks green.

## Releasing

1. Bump `version` in `pyproject.toml`.
2. Move the `[Unreleased]` section of `CHANGELOG.md` under the new version, with today's date.
3. `pip install build twine` — neither is in the `dev` extra — then `python -m build && twine check dist/*`.
4. Tag with the version you just set: `git tag v<version> && git push origin v<version>`.
5. Publish: `python -m twine upload dist/*`.

The one-line installers fetch `main`, so a release reaches users without touching their command. See "Pinning the bootstrap"
in the [README](../README.md) for reproducible installs.
