"""Owner-only permissions for the bash session snapshot (``hermes-snap-*.sh``).

``init_session()`` captures the user's *full login environment, unredacted* into the
snapshot, and ``execute()`` ``source``s that file on every command instead of paying for
``bash -l``. Being unredacted is by design — the environment genuinely has to be
restorable, secrets included — but it means the file is a credential store and must be
readable by nobody except the user who owns the session.

What went wrong (LIM-82 / LIM-90): the snapshot was created with no DACL of its own, so it
silently inherited whatever the temp directory granted. On a host where the temp dir
carries an explicit ACE for a sandbox group (``CodexSandboxUsers`` -> ``ReadAndExecute``,
or ``Modify`` on ``%TEMP%``), any process running as that group could list the directory
and read live provider keys — and with ``Modify`` plus the per-command ``source``, write
to the snapshot and get code execution in the next command.

On POSIX this is already covered: the scripts set ``umask 077`` before ``mktemp``, so the
file is created ``0600`` and ``mv`` preserves the mode. On Windows file modes do not map
to ACLs — ``chmod 600`` on an MSYS mount leaves the inherited ACE exactly where it was —
so the file needs an explicit, inheritance-disabled DACL naming only its owner.

Two call sites cooperate:

* the snapshot shell scripts run :func:`snapshot_harden_shell_command` immediately after
  ``mktemp`` and *before* a single byte of environment is written, so the file is never
  present with contents under the inherited grant;
* :func:`harden_snapshot_file` re-asserts the same DACL from Python once the snapshot is
  published, and :func:`snapshot_dacl_is_owner_only` is the check operators and tests use.

Everything here is best-effort: failing to tighten the DACL must never break command
execution, so the helpers swallow errors and report success as a bool.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

# FILE_ALL_ACCESS — the grant ``icacls /grant:r "<user>":F`` writes.
_FILE_ALL_ACCESS = 0x001F01FF


def _is_windows() -> bool:
    """True when the *host running Hermes* is Windows (patched in tests)."""
    return os.name == "nt" or sys.platform.startswith("win")


def current_user_sid() -> str:
    """String SID of the user running Hermes, or ``""`` when it cannot be resolved."""
    if not _is_windows():
        return ""
    try:
        import win32con
        import win32process
        import win32security

        token = win32security.OpenProcessToken(
            win32process.GetCurrentProcess(), win32con.TOKEN_QUERY)
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        return win32security.ConvertSidToStringSid(sid)
    except Exception:
        logger.debug("could not resolve the current user SID", exc_info=True)
        return ""


def snapshot_harden_shell_command(tmp_path_var: str = '"$__hermes_snap_tmp"') -> str:
    """Shell snippet pinning the snapshot temp file to its owner, or ``""`` when the file
    mode already does the job.

    Called immediately after ``mktemp`` in both snapshot scripts (bootstrap and the
    per-command re-dump), so the file is never readable by anyone else while it holds
    secrets. The temp file is then ``mv``-ed over the snapshot path, and a rename within
    the same directory keeps the DACL — which is why hardening the temp file is enough.

    ``cygpath -w`` is required because Hermes spawns bash with ``MSYS_NO_PATHCONV=1``
    (native switches like ``icacls /inheritance:r`` must survive), which also means a
    ``/c/...`` path handed to a native binary is *not* translated: icacls rejects it.
    """
    if not _is_windows():
        # POSIX: ``umask 077`` + mktemp already yields 0600, and mv preserves the mode. No
        # extra process spawn on the per-command path.
        return ""
    sid = current_user_sid()
    grant = f"'*{sid}:F'" if sid else '"$USERNAME:F"'
    return (
        f'icacls "$(cygpath -w {tmp_path_var})" /inheritance:r '
        f"/grant:r {grant} >/dev/null 2>&1 || true"
    )


def harden_snapshot_file(path) -> bool:
    """Force *path* to be readable and writable by its owner only; return success.

    Windows: rewrite the DACL with a single ACE for the current user and disable
    inheritance, so an inherited sandbox grant can no longer reach the file. POSIX: 0600.
    """
    try:
        if _is_windows():
            return _harden_windows(str(path))
        os.chmod(path, 0o600)
        return True
    except Exception:
        logger.debug("could not tighten permissions on %s", path, exc_info=True)
        return False


def _harden_windows(path: str) -> bool:
    sid_text = current_user_sid()
    if not sid_text:
        return False
    import win32security

    sid = win32security.ConvertStringSidToSid(sid_text)
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, _FILE_ALL_ACCESS, sid)
    sd = win32security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    sd.SetSecurityDescriptorOwner(sid, 0)
    win32security.SetFileSecurity(
        path,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION
        | win32security.OWNER_SECURITY_INFORMATION,
        sd)
    return True


def snapshot_dacl_is_owner_only(path) -> "bool | None":
    """True when *path* carries an inheritance-disabled DACL naming only its owner.

    ``None`` when the question cannot be answered here (unreadable ACL, filesystem without
    Windows security descriptors, non-Windows platform check failure) — callers should
    treat ``None`` as "unknown", never as success.
    """
    try:
        if _is_windows():
            return _windows_dacl_is_owner_only(str(path))
        return (os.stat(path).st_mode & 0o077) == 0
    except Exception:
        logger.debug("could not read the DACL of %s", path, exc_info=True)
        return None


def _windows_dacl_is_owner_only(path: str) -> "bool | None":
    sid_text = current_user_sid()
    if not sid_text:
        return None
    import win32security

    sd = win32security.GetNamedSecurityInfo(
        path, win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.OWNER_SECURITY_INFORMATION)
    if not sd.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED:
        return False  # inherits from the parent directory — exactly the defect
    dacl = sd.GetSecurityDescriptorDacl()
    if dacl is None:
        return False
    owner = win32security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner())
    if owner != sid_text:
        return False
    for index in range(dacl.GetAceCount()):
        ace_sid = dacl.GetAce(index)[2]
        if win32security.ConvertSidToStringSid(ace_sid) != sid_text:
            return False
    return True
