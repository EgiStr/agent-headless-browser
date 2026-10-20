"""Runtime settings, read once from environment variables."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 9333
DEFAULT_HOME = Path.home() / ".agent-headless-browser"


@dataclass(frozen=True)
class Settings:
    """Where the agent Chrome lives and how it is started.

    Attributes:
        port: CDP port. Chrome always binds it to 127.0.0.1.
        profile_dir: The one profile that holds the Google session. Never copy it.
        out_dir: Where exports and the health log go.
        chrome_args: Extra Chrome flags, e.g. ``--no-sandbox`` inside a container.
        health_url: Page that needs the session; used by health checks.
    """

    port: int = DEFAULT_PORT
    profile_dir: Path = DEFAULT_HOME / "profile"
    out_dir: Path = Path("out")
    chrome_args: tuple[str, ...] = ()
    health_url: str = "https://myaccount.google.com/"

    @property
    def cdp_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        base = cls()
        return cls(
            port=int(env.get("AHB_PORT", base.port)),
            profile_dir=Path(env.get("AHB_PROFILE_DIR", base.profile_dir)).expanduser().resolve(),
            out_dir=Path(env.get("AHB_OUT_DIR", base.out_dir)).expanduser(),
            chrome_args=tuple(shlex.split(env.get("AHB_CHROME_ARGS", ""))),
            health_url=env.get("AHB_HEALTH_URL", base.health_url),
        )
