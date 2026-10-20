## What and why

<!-- What does this change, and what problem does it solve? Link the issue if there is one. -->

## How it was tested

<!-- Commands you ran and what you saw. -->

## Checklist

- [ ] `ruff check . && ruff format --check . && mypy && pytest` pass
- [ ] `AHB_E2E=1 pytest -m integration` passes, if it touches Chrome/CDP code
- [ ] A test covers the change
- [ ] `CHANGELOG.md` updated under *Unreleased*, if users would notice
- [ ] No profile, cookies, exports, real emails or report IDs in the diff
