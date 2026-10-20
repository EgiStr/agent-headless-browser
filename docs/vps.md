# Running on a CLI-only Linux server

Tested in a display-less Debian container: Chrome 155, no X server, no `DISPLAY`. Headless sign-in, a
graceful restart, and a full 212,117-row Looker export all worked. It has **not** been tested yet on a real datacenter
VPS. There Google may ask you to verify a new device or location, and you finish that step in `login-view` too.

## Sizing

Measure by USS, not by summed RSS (see [benchmark.md](benchmark.md)). One Chrome with a few tabs uses 250–350 MB, and
Python plus the MCP server add about 60 MB. A 2 GB VPS is comfortable; 1 GB is tight.

## Setup (systemd, recommended)

Run everything as a normal user. Do not use root, so Chrome's sandbox stays on.

```bash
# Google Chrome
wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt-get install -y ./google-chrome-stable_current_amd64.deb

pipx install git+https://github.com/EgiStr/agent-headless-browser

# One-time sign-in, headless
ahb start --login
ahb login-view                      # leave running
```

On your PC:

```bash
ssh -N -L 9500:127.0.0.1:9500 user@your-vps
```

Open http://127.0.0.1:9500, click into the page and type to sign in. When the header shows
`signed in: ...`, stop `login-view` with Ctrl+C. Then:

```bash
ahb stop
mkdir -p ~/.config/systemd/user
cp deploy/systemd/* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ahb-chrome.service ahb-health.timer
loginctl enable-linger "$USER"      # keep running after logout
journalctl --user -u ahb-health -f  # health results
```

If `ahb` is not in `~/.local/bin`, edit `ExecStart` in the unit files.

## Using it from your PC

CDP stays on the server's 127.0.0.1, so tunnel it:

```bash
ssh -N -L 9333:127.0.0.1:9333 user@your-vps
```

Local tools can then use `http://127.0.0.1:9333`. You can also run `ahb-mcp` on the server and drive it from an MCP
client over SSH.

## Re-login

When `ahb health` starts failing, run `ahb stop && ahb start --login && ahb login-view` and sign in again. Through MCP,
`login_begin` returns the same steps on a server without a display.
