"""The two secrets of the owner-side programs, read where Windows keeps them. Never printed.

    YQ_BACKUP_PASSWORD   the 7-Zip password of the backups (scripts/yq_load_go.py, scripts/yq_backup.py)
    YQ_RESEND_API_KEY    the sending-only key of the brief e-mail (scripts/yq_send.py)

Where a secret is looked for, in this order:
  1. Windows Credential Manager, a generic credential named after it. The owner sets it once, in a
     Command Prompt or PowerShell window of his own:   cmdkey /generic:YQ_BACKUP_PASSWORD /user:yq /pass
     (with /pass and no value, cmdkey asks for the value and does not show it; nothing lands in a
     history file). Windows keeps it encrypted for his account.
  2. (the backup password only) the user environment in the registry, HKCU\\Environment, read
     fresh: a value set later with setx works at once, without restarting Claude Code.
  3. (the backup password only) this process's environment.
The project's .env is never read for these: it sits in OneDrive.

Only the NAME of the place a secret came from is ever written anywhere (credential_manager,
user_environment, process_environment). YQ_SECRETS_OFF=1 (the tests set it) turns every lookup
off, so no test can ever read a real secret of this PC.
"""
from __future__ import annotations

import ctypes
import os
import sys

BACKUP_PASSWORD = "YQ_BACKUP_PASSWORD"
RESEND_API_KEY = "YQ_RESEND_API_KEY"
_CRED_TYPE_GENERIC = 1


def _off() -> bool:
    return os.environ.get("YQ_SECRETS_OFF") == "1"


def read_credential(target: str) -> str | None:
    """The secret of a generic credential in Windows Credential Manager, or None (not set, another
    system, or lookups turned off). The blob is the UTF-16 text cmdkey stored."""
    if _off() or sys.platform != "win32":
        return None

    class _Credential(ctypes.Structure):
        _fields_ = [("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32), ("TargetName", ctypes.c_wchar_p),
                    ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_uint64),
                    ("CredentialBlobSize", ctypes.c_uint32), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", ctypes.c_uint32), ("AttributeCount", ctypes.c_uint32),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p),
                    ("UserName", ctypes.c_wchar_p)]

    try:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        read = advapi.CredReadW
        read.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                         ctypes.POINTER(ctypes.POINTER(_Credential))]
        read.restype = ctypes.c_bool
        free = advapi.CredFree
        free.argtypes = [ctypes.c_void_p]
        found = ctypes.POINTER(_Credential)()
        if not read(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
            return None
        try:
            size = int(found.contents.CredentialBlobSize)
            if size <= 0:
                return None
            raw = ctypes.string_at(found.contents.CredentialBlob, size)
        finally:
            free(found)
    except (OSError, AttributeError, ValueError):
        return None
    try:
        text = raw.decode("utf-16-le")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    text = text.strip("\x00").strip()
    return text or None


def _user_environment(name: str) -> str | None:
    """HKCU\\Environment\\<name>, read fresh (setx writes there)."""
    if _off() or sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    text = str(value or "").strip()
    return text or None


def backup_password(env: dict | None = None) -> tuple[str | None, str | None]:
    """(the backup password, the name of where it came from) or (None, None)."""
    if _off():
        return None, None
    found = read_credential(BACKUP_PASSWORD)
    if found:
        return found, "credential_manager"
    found = _user_environment(BACKUP_PASSWORD)
    if found:
        return found, "user_environment"
    found = str((env if env is not None else os.environ).get(BACKUP_PASSWORD) or "").strip()
    if found:
        return found, "process_environment"
    return None, None


def resend_key() -> str | None:
    """The sending-only key of the brief e-mail, from Credential Manager only."""
    return read_credential(RESEND_API_KEY)
