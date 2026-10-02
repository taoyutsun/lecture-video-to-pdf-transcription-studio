from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


class CredentialError(RuntimeError):
    pass


class WindowsCredentialStore:
    """Use generic Windows credentials; never fall back to a plaintext file."""

    def __init__(self, namespace: str = "LectureVideo2PDF/ASR"):
        self.namespace = namespace

    @property
    def available(self) -> bool:
        return sys.platform == "win32"

    def _api(self):
        if not self.available:
            raise CredentialError("此平台尚未提供安全金鑰保存，請使用僅本次使用模式。")

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        api = ctypes.WinDLL("advapi32", use_last_error=True)
        api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(Credential))]
        api.CredReadW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        return api, Credential

    def get(self, account: str) -> str | None:
        api, credential_type = self._api()
        credential = ctypes.POINTER(credential_type)()
        if not api.CredReadW(f"{self.namespace}/{account}", 1, 0, ctypes.byref(credential)):
            code = ctypes.get_last_error()
            if code == 1168:
                return None
            raise CredentialError(f"無法讀取 Windows 憑證管理員（錯誤 {code}）。")
        try:
            return ctypes.string_at(credential.contents.CredentialBlob,
                                    credential.contents.CredentialBlobSize).decode("utf-8")
        finally:
            api.CredFree(credential)

    def set(self, account: str, secret: str) -> None:
        api, credential_type = self._api()
        encoded = secret.encode("utf-8")
        if not encoded or len(encoded) > 2560:
            raise CredentialError("金鑰不能為空，且最多可保存 2560 bytes。")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = credential_type()
        credential.Type = 1
        credential.TargetName = f"{self.namespace}/{account}"
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = blob
        credential.Persist = 2  # Local machine persistence, scoped to the current user.
        credential.UserName = "LectureVideo2PDF"
        if not api.CredWriteW(ctypes.byref(credential), 0):
            raise CredentialError(f"無法保存 Windows 憑證（錯誤 {ctypes.get_last_error()}）。未保存明文金鑰。")

    def delete(self, account: str) -> None:
        api, _ = self._api()
        if not api.CredDeleteW(f"{self.namespace}/{account}", 1, 0):
            code = ctypes.get_last_error()
            if code != 1168:
                raise CredentialError(f"無法刪除 Windows 憑證（錯誤 {code}）。")
