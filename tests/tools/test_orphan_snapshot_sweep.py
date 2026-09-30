"""Orphaned ``hermes-snap-*.sh`` files must not outlive the session that made them.

``cleanup()`` only runs on graceful teardown, and ``cleanup_terminal_temp_cache()`` prunes
only the managed ``HERMES_HOME/cache/terminal`` dir on a 24h idle timer — so a killed or
crashed Hermes process leaves the snapshot (the full unredacted login environment) wherever
its temp dir was, with nothing to reap it. ``cleanup_orphan_snapshots()`` closes that: the
snapshot name carries the creating pid, so a dead owner means an orphan (LIM-90).
"""

import os
import subprocess
import sys
import time

import pytest

from tools.environments import local as local_mod


def _dead_pid() -> int:
    """A pid that is guaranteed not to be running: a reaped child."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _age(path, seconds: float) -> None:
    when = time.time() - seconds
    os.utime(path, (when, when))


@pytest.fixture()
def sweep_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(local_mod, "_snapshot_sweep_roots", lambda: [tmp_path])
    return tmp_path


def test_dead_owner_snapshot_is_removed(sweep_dir):
    orphan = sweep_dir / f"hermes-snap-{_dead_pid()}-abcdef123456.sh"
    orphan.write_text("export SECRET=1\n")
    _age(orphan, 120)
    assert local_mod.cleanup_orphan_snapshots() == 1
    assert not orphan.exists()


def test_live_owner_snapshot_survives_however_old(sweep_dir):
    """The sweep must never eat a running session's file — that process is still sourcing it."""
    mine = sweep_dir / f"hermes-snap-{os.getpid()}-abcdef123456.sh"
    mine.write_text("export MINE=1\n")
    _age(mine, 100 * 3600)
    assert local_mod.cleanup_orphan_snapshots() == 0
    assert mine.exists()


def test_fresh_snapshot_is_left_alone_even_without_an_owner(sweep_dir):
    """A bootstrap in flight owns a file that may momentarily look ownerless."""
    fresh = sweep_dir / f"hermes-snap-{_dead_pid()}-abcdef123456.sh"
    fresh.write_text("x")
    assert local_mod.cleanup_orphan_snapshots() == 0
    assert fresh.exists()


def test_legacy_name_without_a_pid_falls_back_to_idleness(sweep_dir):
    stale = sweep_dir / "hermes-snap-abcdef123456.sh"
    stale.write_text("x")
    _age(stale, 5 * 3600)  # past SNAPSHOT_MAX_IDLE_HOURS (4h), under the 24h cache timer
    keep = sweep_dir / "hermes-snap-fedcba654321.sh"
    keep.write_text("x")
    _age(keep, 60 * 60)
    assert local_mod.cleanup_orphan_snapshots() == 1
    assert not stale.exists() and keep.exists()


def test_retention_is_capped_however_the_caller_asks(sweep_dir):
    """The gateway housekeeping loop calls every cleaner with max_age_hours=24; a file with
    live credentials must not be given a day of grace because of that."""
    stale = sweep_dir / "hermes-snap-abcdef123456.sh"
    stale.write_text("x")
    _age(stale, 6 * 3600)
    assert local_mod.cleanup_orphan_snapshots(max_age_hours=24) == 1
    assert not stale.exists()


def test_partial_temp_files_of_a_dead_session_are_swept_too(sweep_dir):
    leftover = sweep_dir / f"hermes-snap-{_dead_pid()}-abcdef123456.sh.tmp.ab12cd"
    leftover.write_text("x")
    _age(leftover, 120)
    assert local_mod.cleanup_orphan_snapshots() == 1
    assert not leftover.exists()


def test_unrelated_files_and_directories_are_untouched(sweep_dir):
    keepers = [sweep_dir / "hermes-cwd-abcdef123456.txt", sweep_dir / "notes.sh",
               sweep_dir / "hermes-snap-not-a-session.sh", sweep_dir / "hermes_bg_x.log"]
    for path in keepers:
        path.write_text("x")
        _age(path, 100 * 3600)
    assert local_mod.cleanup_orphan_snapshots() == 0
    assert all(path.exists() for path in keepers)


def test_startup_sweep_runs_once_and_reports_removed(sweep_dir, monkeypatch):
    monkeypatch.setattr(local_mod, "_terminal_temp_pruned_once", False)
    orphan = sweep_dir / f"hermes-snap-{_dead_pid()}-abcdef123456.sh"
    orphan.write_text("x")
    _age(orphan, 120)
    local_mod._prune_terminal_temp_once()
    assert not orphan.exists()
    # A second call is a no-op: the once-per-process guard still stands.
    again = sweep_dir / f"hermes-snap-{_dead_pid()}-abcdef123456.sh"
    again.write_text("x")
    _age(again, 120)
    local_mod._prune_terminal_temp_once()
    assert again.exists()


def test_used_pid_detection_helper_is_honest():
    assert local_mod._pid_alive(os.getpid()) is True
    assert local_mod._pid_alive(_dead_pid()) is False
    assert local_mod._pid_alive(0) is False


def test_sweep_roots_include_the_process_temp_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TERMINAL_TEMP_DIR", str(tmp_path / "terminal"))
    (tmp_path / "terminal").mkdir()
    roots = [str(path).lower() for path in local_mod._snapshot_sweep_roots()]
    assert str(tmp_path).lower() in roots
    assert str(tmp_path / "terminal").lower() in roots


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
