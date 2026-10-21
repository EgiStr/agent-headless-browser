"""The installer's decisions, without touching the machine.

The installer writes YAML into a profile file that the user did not ask it to
rewrite, so the insert point and the idempotency are worth testing directly.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install.py"

PATCH = """\
- insert:
    # a comment inside the insert list
    - id: mcp-browser
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: browser-mcp
- id: ui-settings-general
  name: "@deepseek-ai/dsh-client-ui-settings-general"
  config:
    welcomeNoticeVersion: 2026-09-28.1
"""


def _load_installer():
    spec = importlib.util.spec_from_file_location("ahb_install", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


install = _load_installer()


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Python 3.12.1", (3, 12, 1)), ("3.10", (3, 10)), ("no version here", ())],
)
def test_parse_version(text, expected):
    assert install.parse_version(text) == expected


def test_python_is_supported():
    assert install.python_is_supported((3, 10))
    assert install.python_is_supported((3, 13, 0))
    assert not install.python_is_supported((3, 9))
    assert not install.python_is_supported(())


def test_find_dsh_insert_index_points_at_the_next_top_level_entry():
    lines = PATCH.splitlines(keepends=True)
    index = install.find_dsh_insert_index(lines)
    assert lines[index].startswith("- id: ui-settings-general")


def test_find_dsh_insert_index_appends_when_the_insert_list_is_last():
    lines = "- insert:\n    - id: mcp-browser\n      name: x\n".splitlines(keepends=True)
    assert install.find_dsh_insert_index(lines) == len(lines)


def test_find_dsh_insert_index_without_an_insert_key():
    lines = ["- id: something-else\n"]
    assert install.find_dsh_insert_index(lines) == len(lines)


def test_dsh_entry_block_uses_the_names_the_server_reads():
    profile = Path("/home/me/p")
    block = install.dsh_entry_block(Path("/usr/local/bin/ahb-mcp"), profile, Path("out"))
    assert "- id: mcp-agent-browser" in block
    assert "serverName: agent-browser" in block
    assert "AHB_PORT: '9333'" in block
    assert f"AHB_PROFILE_DIR: '{profile}'" in block
    body = [line for line in block.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert all(line.startswith("    ") for line in body)


def test_dsh_entry_is_detected():
    assert install.dsh_entry_present(PATCH) is False
    block = install.dsh_entry_block(Path("x"), Path("y"), Path("z"))
    assert install.dsh_entry_present(PATCH + block) is True


def test_configure_dsh_adds_the_entry_once(tmp_path, monkeypatch):
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text(PATCH, encoding="utf-8")
    monkeypatch.setattr(install, "dsh_patch_files", lambda: [patch])
    console = install.Console(quiet=True)
    executable = Path("/usr/local/bin/ahb-mcp")

    assert install.configure_dsh(executable, Path("/home/me/p"), Path("out"), console, False)
    text = patch.read_text(encoding="utf-8")
    assert text.count("- id: mcp-agent-browser") == 1
    # the new entry belongs to the insert list, not to the top level
    assert text.index("- id: mcp-agent-browser") < text.index("- id: ui-settings-general")

    # a second run must not add it again
    install.configure_dsh(executable, Path("/home/me/p"), Path("out"), console, False)
    assert patch.read_text(encoding="utf-8").count("- id: mcp-agent-browser") == 1


def test_configure_dsh_dry_run_leaves_the_file_alone(tmp_path, monkeypatch):
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text(PATCH, encoding="utf-8")
    monkeypatch.setattr(install, "dsh_patch_files", lambda: [patch])
    console = install.Console(quiet=True)

    install.configure_dsh(Path("x"), Path("y"), Path("z"), console, True)
    assert patch.read_text(encoding="utf-8") == PATCH


EMPTY_PATCH = """\
# Your patch layer for this dsh profile, applied after every bundle layer.
# a top-level YAML array of loader patch entries (id-targeted config
# overrides, disables, and insert lists; `!!js` expressions allowed).
[]
"""

BLOCK_PATCH = """\
# a top-level YAML array of loader patch entries.
- id: ui-settings-general
  name: "@deepseek-ai/dsh-client-ui-settings-general"
  config:
    welcomeNoticeVersion: 2026-09-28.1
"""


def _parse(text: str):
    class Loader(yaml.SafeLoader):
        pass

    Loader.add_constructor("tag:yaml.org,2002:js", lambda loader, node: str(loader.construct_scalar(node)))
    return yaml.load(text, Loader=Loader)


def test_configure_dsh_replaces_an_empty_array_instead_of_appending(tmp_path, monkeypatch):
    """Appending after '[]' makes the file invalid YAML: a flow sequence cannot
    be followed by a block sequence."""
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text(EMPTY_PATCH, encoding="utf-8")
    monkeypatch.setattr(install, "dsh_patch_files", lambda: [patch])
    console = install.Console(quiet=True)

    assert install.configure_dsh(Path("/bin/ahb-mcp"), Path("/p"), Path("out"), console, False)
    text = patch.read_text(encoding="utf-8")
    doc = _parse(text)
    assert isinstance(doc, list)
    assert len(doc) == 1
    entries = doc[0]["insert"]
    assert [e["id"] for e in entries] == ["mcp-agent-browser"]


def test_configure_dsh_appends_to_a_block_array(tmp_path, monkeypatch):
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text(BLOCK_PATCH, encoding="utf-8")
    monkeypatch.setattr(install, "dsh_patch_files", lambda: [patch])
    console = install.Console(quiet=True)

    assert install.configure_dsh(Path("/bin/ahb-mcp"), Path("/p"), Path("out"), console, False)
    doc = _parse(patch.read_text(encoding="utf-8"))
    assert isinstance(doc, list)
    assert len(doc) == 2
    assert doc[0]["id"] == "ui-settings-general"
    assert [e["id"] for e in doc[1]["insert"]] == ["mcp-agent-browser"]


def test_configure_dsh_keeps_a_backup(tmp_path, monkeypatch):
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text(EMPTY_PATCH, encoding="utf-8")
    monkeypatch.setattr(install, "dsh_patch_files", lambda: [patch])
    console = install.Console(quiet=True)

    install.configure_dsh(Path("/bin/ahb-mcp"), Path("/p"), Path("out"), console, False)
    backup = patch.with_name(patch.name + ".install.bak")
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == EMPTY_PATCH


def test_dsh_structure_detects_both_shapes():
    assert install.dsh_structure(EMPTY_PATCH.splitlines(keepends=True)) == "toplevel"
    assert install.dsh_structure(BLOCK_PATCH.splitlines(keepends=True)) == "toplevel"
    assert install.dsh_structure(PATCH.splitlines(keepends=True)) == "insert"


def test_parse_status_reads_signed_in_accounts():
    status = install.parse_status('{"running": true, "google_accounts": ["a@b.c"], "headless": true}')
    assert status.running is True
    assert status.signed_in is True
    assert status.accounts == ["a@b.c"]


def test_parse_status_on_running_but_signed_out():
    status = install.parse_status('{"running": true, "google_accounts": []}')
    assert status.running is True
    assert status.signed_in is False


def test_parse_status_on_garbage():
    status = install.parse_status("not json at all")
    assert status.running is False
    assert status.accounts == []


def test_resolve_executable_finds_a_script_on_the_path(tmp_path, monkeypatch):
    # A name no real install can have, so the fixture is the only candidate.
    # On Windows shutil.which only matches a name in PATHEXT, so the file needs
    # the .exe suffix there; on POSIX it only matches an executable file.
    name = "ahb-mcp-fixture.exe" if os.name == "nt" else "ahb-mcp-fixture"
    script = tmp_path / name
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    if os.name != "nt":
        script.chmod(0o755)
    # Replace PATH instead of prepending to it: prepending would leave the
    # real ahb-mcp console script visible, and on a machine that installed the
    # package that one wins the lookup.
    monkeypatch.setenv("PATH", str(tmp_path))
    assert install.resolve_executable(Path(name).stem) == script


def test_running_agent_profile_reads_the_user_data_dir(monkeypatch):
    monkeypatch.setattr(install, "cdp_is_up", lambda port: True)
    monkeypatch.setattr(
        install,
        "process_command_lines",
        lambda: [
            "chrome.exe --user-data-dir=/tmp/agent --remote-debugging-port=9333 --headless=new",
            "chrome.exe --type=gpu-process",
        ],
    )
    assert install.running_agent_profile(9333) == Path("/tmp/agent")


def test_running_agent_profile_is_none_when_cdp_is_down(monkeypatch):
    monkeypatch.setattr(install, "cdp_is_up", lambda port: False)
    assert install.running_agent_profile(9333) is None


def test_choose_profile_dir_prefers_the_explicit_path(monkeypatch):
    monkeypatch.delenv("AHB_PROFILE_DIR", raising=False)
    assert install.choose_profile_dir("/tmp/explicit", 9333) == Path("/tmp/explicit")


def test_choose_profile_dir_uses_the_environment(monkeypatch):
    monkeypatch.setenv("AHB_PROFILE_DIR", "/tmp/from-env")
    assert install.choose_profile_dir(None, 9333) == Path("/tmp/from-env")


def test_choose_profile_dir_reuses_the_running_agent_profile(monkeypatch, tmp_path):
    monkeypatch.delenv("AHB_PROFILE_DIR", raising=False)
    monkeypatch.setattr(install, "running_agent_profile", lambda port: tmp_path / "agent")
    assert install.choose_profile_dir(None, 9333) == tmp_path / "agent"


def test_verify_uses_the_cli_not_the_mcp_server(monkeypatch):
    """ahb-mcp is the MCP server; only 'ahb status' prints the status JSON."""
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, '{"running": true, "google_accounts": ["a@b.c"]}', "")

    monkeypatch.setattr(install, "run", fake_run)
    cli = Path("/usr/local/bin/ahb")
    status = install.verify(cli, install.Console(quiet=True), False)
    assert seen["command"] == [str(cli), "status"]
    assert status.signed_in is True
