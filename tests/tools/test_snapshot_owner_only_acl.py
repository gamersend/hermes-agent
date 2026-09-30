"""The session snapshot must be readable by its owner only.

``hermes-snap-*.sh`` holds the full login environment *unredacted* — that is by design, so
that ``source`` can replace ``bash -l`` — which makes it a credential file. It used to be
created with no DACL of its own, so on Windows it silently inherited whatever the temp
directory granted; a temp dir with an explicit ACE for a sandbox group
(``CodexSandboxUsers`` -> ReadAndExecute, or Modify on ``%TEMP%``) made live provider keys
readable, and with Modify writable — the per-command ``source`` turns that into code
execution. These tests pin the fix (LIM-90): an inheritance-disabled, owner-only ACL set on
the temp file *before* the first byte of environment is written.
"""

import os
import sys
from pathlib import Path

import pytest

from tools.environments import snapshot_acl
from tools.environments.base_session_env import (
    _snapshot_bootstrap_script, _wrap_command_script)

SID = "S-1-5-21-2612376461-3176308488-3057092739-1001"
HARDEN = "HARDEN-MARKER-LINE"


class TestShellHardeningCommand:
    def test_posix_returns_nothing_because_umask_covers_it(self, monkeypatch):
        monkeypatch.setattr(snapshot_acl, "_is_windows", lambda: False)
        assert snapshot_acl.snapshot_harden_shell_command() == ""

    def test_windows_disables_inheritance_and_grants_only_the_owner(self, monkeypatch):
        monkeypatch.setattr(snapshot_acl, "_is_windows", lambda: True)
        monkeypatch.setattr(snapshot_acl, "current_user_sid", lambda: SID)
        cmd = snapshot_acl.snapshot_harden_shell_command()
        assert "/inheritance:r" in cmd
        assert f"/grant:r '*{SID}:F'" in cmd
        assert cmd.rstrip().endswith("|| true")  # never breaks command execution

    def test_windows_needs_a_native_path_because_msys_pathconv_is_off(self, monkeypatch):
        """Hermes runs bash with MSYS_NO_PATHCONV=1, so a /c/... path reaches native icacls
        unconverted and is rejected — the snippet must translate with cygpath."""
        monkeypatch.setattr(snapshot_acl, "_is_windows", lambda: True)
        monkeypatch.setattr(snapshot_acl, "current_user_sid", lambda: SID)
        cmd = snapshot_acl.snapshot_harden_shell_command()
        assert 'icacls "$(cygpath -w "$__hermes_snap_tmp")"' in cmd

    def test_windows_falls_back_to_the_leaf_name_without_a_sid(self, monkeypatch):
        monkeypatch.setattr(snapshot_acl, "_is_windows", lambda: True)
        monkeypatch.setattr(snapshot_acl, "current_user_sid", lambda: "")
        cmd = snapshot_acl.snapshot_harden_shell_command()
        assert '"$USERNAME:F"' in cmd
        assert "*:" not in cmd


class TestScriptWiring:
    def test_bootstrap_hardens_before_the_first_env_byte(self):
        script = _snapshot_bootstrap_script(
            quoted_cwd="'/tmp'", quoted_snap="'/tmp/s.sh'",
            snap_tmp_template="'/tmp/s.sh.tmp.XXXXXXXXXX'", excluded_names=(),
            cwd_marker="__M__", harden_cmd=HARDEN)
        assert HARDEN in script
        assert script.index("mktemp") < script.index(HARDEN) < script.index("export -p")

    def test_per_command_redump_hardens_its_own_temp_file(self):
        """The env is re-dumped on every command, so the published snapshot is replaced
        each time — the grant has to be re-asserted there too."""
        script = _wrap_command_script(
            "true", quoted_cwd="'/tmp'", quoted_snap="'/tmp/s.sh'",
            snap_tmp_template="'/tmp/s.sh.tmp.XXXXXXXXXX'", passthrough_names=(),
            snapshot_ready=True, cwd_marker="__M__", harden_cmd=HARDEN)
        dump_line = next(line for line in script.splitlines() if "__hermes_snap_tmp=$(mktemp" in line)
        assert HARDEN in dump_line
        assert dump_line.index("mktemp") < dump_line.index(HARDEN) < dump_line.index("mv -f")

    def test_no_hardening_snippet_on_backends_that_do_not_supply_one(self):
        script = _wrap_command_script(
            "true", quoted_cwd="'/tmp'", quoted_snap="'/tmp/s.sh'",
            snap_tmp_template="'/tmp/s.sh.tmp.XXXXXXXXXX'", passthrough_names=(),
            snapshot_ready=True, cwd_marker="__M__")
        assert "icacls" not in script
        assert "$(mktemp '/tmp/s.sh.tmp.XXXXXXXXXX') && { {" in script

    @pytest.mark.platforms("windows")
    def test_local_environment_supplies_the_windows_snippet(self):
        from tools.environments.local import LocalEnvironment

        env = LocalEnvironment.__new__(LocalEnvironment)
        assert "icacls" in env._snapshot_harden_shell()


class TestSnapshotNameCarriesItsOwner:
    def test_snapshot_path_embeds_the_creating_pid(self):
        """The pid is what lets the sweep tell an orphan from a live session, so it must be
        part of the name rather than inferred."""
        from tools.environments.base import BaseEnvironment

        class _Probe(BaseEnvironment):
            def _run_bash(self, *a, **kw):  # pragma: no cover - construction only
                raise AssertionError

            def cleanup(self):  # pragma: no cover
                pass

            def get_temp_dir(self):
                return "/tmp"

        probe = _Probe(cwd="/tmp", timeout=1)
        assert probe._snapshot_path == f"/tmp/hermes-snap-{os.getpid()}-{probe._session_id}.sh"
        assert len(probe._session_id) == 12 and probe._session_id.isalnum()


@pytest.mark.platforms("windows")
class TestWindowsAclAppliedToRealFile:
    def test_hardened_file_loses_inherited_aces(self, tmp_path):
        """The real thing: a file in a directory whose ACL grants a sandbox group must end
        up carrying only the owner."""
        target = tmp_path / "hermes-snap-1234-abcdef123456.sh"
        target.write_text("export LEAKED=1\n")
        assert snapshot_acl.snapshot_dacl_is_owner_only(target) is False  # inherits
        assert snapshot_acl.harden_snapshot_file(target) is True
        assert snapshot_acl.snapshot_dacl_is_owner_only(target) is True
        # and the contents survive the rewrite
        assert target.read_text() == "export LEAKED=1\n"

    def test_real_shell_snippet_leaves_an_owner_only_file(self, tmp_path):
        """Run the exact snippet the snapshot scripts execute after ``mktemp``: bash with
        MSYS_NO_PATHCONV=1 (as Hermes spawns it), cygpath for the native path, real icacls."""
        import shlex
        import shutil
        import subprocess

        bash = shutil.which("bash")
        if not bash:  # pragma: no cover - Windows test hosts always have Git Bash
            pytest.skip("no bash on PATH")

        target = tmp_path / "hermes-snap-1234-abcdef123456.sh.tmp.ab12cd"
        target.write_text("export SECRET=1\n")
        assert snapshot_acl.snapshot_dacl_is_owner_only(target) is False  # inherited grant
        script = (f"__hermes_snap_tmp={shlex.quote(target.as_posix())}\n"
                  f"{snapshot_acl.snapshot_harden_shell_command()}\n")
        proc = subprocess.run(
            [bash, "-c", script], capture_output=True, text=True,
            env=dict(os.environ, MSYS_NO_PATHCONV="1"), timeout=120)
        assert proc.returncode == 0, proc.stderr
        assert snapshot_acl.snapshot_dacl_is_owner_only(target) is True
        assert target.read_text() == "export SECRET=1\n"  # hardening never truncates


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
