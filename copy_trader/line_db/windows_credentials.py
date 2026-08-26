"""Windows Credential Manager access for the current user's LINE DB key."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", wintypes.LPVOID),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_PCREDENTIALW = ctypes.POINTER(_CREDENTIALW)


def _api():
    if sys.platform != "win32":
        raise RuntimeError("Windows Credential Manager is available only on Windows")
    library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    library.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_PCREDENTIALW),
    ]
    library.CredReadW.restype = wintypes.BOOL
    library.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    library.CredWriteW.restype = wintypes.BOOL
    library.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    library.CredDeleteW.restype = wintypes.BOOL
    library.CredFree.argtypes = [wintypes.LPVOID]
    library.CredFree.restype = None
    return library


def read_generic_credential(target: str) -> str:
    library = _api()
    pointer = _PCREDENTIALW()
    if not library.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            raise RuntimeError(
                f"LINE DB key was not found in Windows Credential Manager (target={target})"
            )
        raise OSError(error, "CredReadW failed")
    try:
        credential = pointer.contents
        blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        try:
            return blob.decode("utf-16-le").rstrip("\0")
        except UnicodeDecodeError:
            return blob.decode("utf-8").rstrip("\0")
    finally:
        library.CredFree(pointer)


def write_generic_credential(target: str, secret: str) -> None:
    library = _api()
    blob = secret.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(blob)
    credential = _CREDENTIALW()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.Comment = "TRADER read-only LINE database key"
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(wintypes.BYTE))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = "LINE database"
    if not library.CredWriteW(ctypes.byref(credential), 0):
        error = ctypes.get_last_error()
        raise OSError(error, "CredWriteW failed")


def delete_generic_credential(target: str) -> bool:
    library = _api()
    if library.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        return True
    error = ctypes.get_last_error()
    if error == ERROR_NOT_FOUND:
        return False
    raise OSError(error, "CredDeleteW failed")
