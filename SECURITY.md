# Security policy

This tool controls a browser that is signed in to a real Google account, so a vulnerability can mean someone taking
over that account.

## Reporting

Please **do not open a public issue**. Report privately through
[GitHub security advisories](https://github.com/EgiStr/agent-headless-browser/security/advisories/new).
I aim to reply within 7 days.

## Scope

Examples of what counts:
- Anything that exposes the CDP port, `login-view`, or the profile beyond 127.0.0.1 by default.
- Credential or cookie leaks into logs, exports, or MCP tool output.
- MCP tools that let a page's content steer the agent into typing credentials.

## Hardening checklist for users

- Keep CDP (9333) and `login-view` (9500) on 127.0.0.1, and reach them only through `ssh -L`.
- Treat the profile directory as a password. Back it up encrypted and never share it.
- Run as a normal user with Chrome's sandbox on. Use `--no-sandbox` only inside containers.
- Stop `login-view` once you are signed in.
