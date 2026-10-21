"""The installer's decisions, without touching the machine.

The installer writes YAML into a profile file that the user did not ask it to
rewrite, so the insert point and the idempotency are worth testing directly.
"""

import importlib.util
import json
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

ROOT_MAP_PATCH = """\
# a top-level mapping with an insert key
insert:
  - id: mcp-browser
    name: '@deepseek-ai/dsh-mcp-client'
    config:
      serverName: browser-mcp
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


def test_configure_dsh_updates_the_entry_it_wrote(tmp_path, monkeypatch):
    """A re-run has to pick up the new path, not leave the old one behind."""
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text(PATCH, encoding="utf-8")
    monkeypatch.setattr(install, "dsh_patch_files", lambda: [patch])
    console = install.Console(quiet=True)

    install.configure_dsh(Path("/bin/ahb-mcp"), Path("/p"), Path("out"), console, False)
    install.configure_dsh(Path("/other/ahb-mcp"), Path("/p2"), Path("out2"), console, False)

    text = patch.read_text(encoding="utf-8")
    assert text.count("- id: mcp-agent-browser") == 1
    assert str(Path("/other/ahb-mcp")) in text
    assert str(Path("/bin/ahb-mcp")) not in text
    doc = _parse(text)
    entry = next(e for e in doc[0]["insert"] if e["id"] == "mcp-agent-browser")
    assert entry["config"]["command"] == str(Path("/other/ahb-mcp"))


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
    assert install.choose_profile_dir("/tmp/explicit", 9333) == Path("/tmp/explicit").resolve()


def test_choose_profile_dir_uses_the_environment(monkeypatch):
    monkeypatch.setenv("AHB_PROFILE_DIR", "/tmp/from-env")
    assert install.choose_profile_dir(None, 9333) == Path("/tmp/from-env").resolve()


def test_choose_profile_dir_resolves_to_an_absolute_path(monkeypatch, tmp_path):
    """The path ends up in the config of a client with another working directory."""
    monkeypatch.delenv("AHB_PROFILE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert install.choose_profile_dir("relative/profile", 9333).is_absolute()


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


def test_yaml_scalar_escapes_a_quote():
    assert install.yaml_scalar("O'Brien") == "'O''Brien'"


def test_dsh_entry_with_an_apostrophe_still_parses(tmp_path, monkeypatch):
    """A profile directory with a quote in its name must not break the patch file."""
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text(PATCH, encoding="utf-8")
    monkeypatch.setattr(install, "dsh_patch_files", lambda: [patch])
    console = install.Console(quiet=True)
    profile = Path("/home/O'Brien/.agent-headless-browser/profile")

    assert install.configure_dsh(Path("/usr/local/bin/ahb-mcp"), profile, Path("out"), console, False)
    doc = _parse(patch.read_text(encoding="utf-8"))
    entry = next(e for e in doc[0]["insert"] if e["id"] == "mcp-agent-browser")
    assert entry["config"]["env"]["AHB_PROFILE_DIR"] == str(profile)


def test_dsh_entry_present_ignores_a_comment():
    """A comment that mentions the id does not make the profile look configured."""
    assert install.dsh_entry_present("# - id: mcp-agent-browser\n") is False
    assert install.dsh_entry_present("    - id: mcp-agent-browser\n") is True


def test_dsh_structure_detects_a_root_mapping():
    lines = "insert:\n  - id: mcp-browser\n    name: x\n".splitlines(keepends=True)
    assert install.dsh_structure(lines) == "rootmap"


def test_configure_dsh_fills_a_root_mapping(tmp_path, monkeypatch):
    """A top-level mapping with an insert key gets the entry in that list."""
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text(ROOT_MAP_PATCH, encoding="utf-8")
    monkeypatch.setattr(install, "dsh_patch_files", lambda: [patch])
    console = install.Console(quiet=True)

    assert install.configure_dsh(Path("/bin/ahb-mcp"), Path("/p"), Path("out"), console, False)
    doc = _parse(patch.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    assert [e["id"] for e in doc["insert"]] == ["mcp-browser", "mcp-agent-browser"]


def test_configure_claude_code_passes_the_environment(monkeypatch):
    """Without the environment the server starts on the default profile instead."""
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(install, "which", lambda name: "/usr/local/bin/claude")
    monkeypatch.setattr(install, "run", fake_run)
    console = install.Console(quiet=True)
    profile = Path("/home/me/p")
    out = Path("/home/me/out")

    assert install.configure_claude_code(Path("/usr/local/bin/ahb-mcp"), profile, out, console, False, 9334)
    command = seen["command"]
    assert command[-1] == str(Path("/usr/local/bin/ahb-mcp"))
    assert "AHB_PORT=9334" in command
    assert f"AHB_PROFILE_DIR={profile}" in command
    assert f"AHB_OUT_DIR={out}" in command


def test_write_generic_snippet_keeps_the_other_servers(tmp_path, monkeypatch):
    """The installer owns its own entry, not the whole file."""
    monkeypatch.setattr(install.Path, "home", classmethod(lambda cls: tmp_path))
    home = tmp_path / ".agent-headless-browser"
    home.mkdir(parents=True)
    target = home / "mcp.json"
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    console = install.Console(quiet=True)

    written = install.write_generic_snippet(
        Path("/usr/local/bin/ahb-mcp"), Path("/p"), Path("/out"), console, 9333
    )
    assert written == target
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "other" in data["mcpServers"]
    assert "agent-browser" in data["mcpServers"]
    assert (home / "mcp.json.install.bak").is_file()


def test_write_generic_snippet_leaves_a_config_it_cannot_read_alone(tmp_path, monkeypatch):
    """The servers in a file that does not parse are unknown, so it is not replaced."""
    monkeypatch.setattr(install.Path, "home", classmethod(lambda cls: tmp_path))
    home = tmp_path / ".agent-headless-browser"
    home.mkdir(parents=True)
    target = home / "mcp.json"
    target.write_text("{not json", encoding="utf-8")
    console = install.Console(quiet=True)

    written = install.write_generic_snippet(
        Path("/usr/local/bin/ahb-mcp"), Path("/p"), Path("/out"), console, 9333
    )
    assert target.read_text(encoding="utf-8") == "{not json"
    assert written != target
    assert written.is_file()


def test_a_second_run_keeps_the_first_backup(tmp_path, monkeypatch):
    """Every run keeps its own backup, so the oldest state is still there."""
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text(PATCH, encoding="utf-8")
    monkeypatch.setattr(install, "dsh_patch_files", lambda: [patch])
    console = install.Console(quiet=True)

    install.configure_dsh(Path("/bin/ahb-mcp"), Path("/p"), Path("out"), console, False)
    install.configure_dsh(Path("/bin/ahb-mcp"), Path("/p"), Path("out"), console, False)
    backups = sorted(p.name for p in tmp_path.glob("cordis.patch.yml.install.bak*"))
    assert backups == ["cordis.patch.yml.install.bak", "cordis.patch.yml.install.bak.1"]


def test_verify_reports_a_failing_command(monkeypatch):
    """A command that fails is not the same as a browser that is not running."""

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "boom")

    monkeypatch.setattr(install, "run", fake_run)
    status = install.verify(Path("/usr/local/bin/ahb"), install.Console(quiet=True), False)
    assert status.error == "boom"
    assert status.running is False


def test_verify_skips_when_the_cli_is_missing(monkeypatch):
    monkeypatch.setattr(install, "which", lambda name: None)

    def boom(*args, **kwargs):
        raise AssertionError("the verification must not run a command it does not have")

    monkeypatch.setattr(install, "run", boom)
    status = install.verify(None, install.Console(quiet=True), False)
    assert status.error is None
    assert status.running is False


def test_verify_uses_the_selected_port_and_profile(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0, '{"running": false}', "")

    monkeypatch.setattr(install, "run", fake_run)
    install.verify(Path("/usr/local/bin/ahb"), install.Console(quiet=True), False, Path("/p"), 9334)
    assert seen["env"]["AHB_PORT"] == "9334"
    assert seen["env"]["AHB_PROFILE_DIR"] == str(Path("/p"))


def test_user_data_dir_survives_spaces():
    """The profile path may contain spaces, and the whole argument is then quoted."""
    line = 'chrome.exe --user-data-dir="C:\\Users\\me\\My Profiles\\agent" --remote-debugging-port=9333'
    assert install.user_data_dir(line) == "C:\\Users\\me\\My Profiles\\agent"
    quoted = '"--user-data-dir=C:\\Users\\me\\My Profiles\\agent" --headless=new'
    assert install.user_data_dir(quoted) == "C:\\Users\\me\\My Profiles\\agent"
    plain = "chrome.exe --user-data-dir=/tmp/agent --remote-debugging-port=9333"
    assert install.user_data_dir(plain) == "/tmp/agent"


def test_port_number_rejects_an_impossible_port():
    assert install.port_number("9333") == 9333
    for bad in ("0", "70000", "abc", "-1"):
        with pytest.raises(install.argparse.ArgumentTypeError):
            install.port_number(bad)


def test_the_installer_and_the_runtime_look_in_the_same_places():
    """A browser the runtime cannot launch is of no use to the user."""
    from agent_headless_browser import chrome

    assert {str(p) for p in install.chrome_paths("Windows")} == {str(p) for p in chrome.WINDOWS_CHROME}
    assert {str(p.expanduser()) for p in install.chrome_paths("Darwin")} == {
        str(p.expanduser()) for p in chrome.MACOS_CHROME
    }
    assert {str(p) for p in install.chrome_paths("Linux")} == {str(p) for p in chrome.LINUX_CHROME_PATHS}
    assert tuple(install.CHROME_COMMANDS["Windows"]) == tuple(chrome.WINDOWS_CHROME_COMMANDS)
    assert tuple(install.CHROME_COMMANDS["Linux"]) == tuple(chrome.LINUX_CHROME)
    # the runtime needs real Chrome, so the installer must not accept Chromium
    assert "chromium" not in install.CHROME_COMMANDS["Linux"]
    assert "chromium-browser" not in install.CHROME_COMMANDS["Linux"]
