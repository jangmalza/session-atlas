"""Database-scoped Windows credentials. No secret is written to app files."""
import ctypes
from ctypes import wintypes
import hashlib
import os


class CredentialUnavailable(RuntimeError):
    pass


class WindowsCredentials:
    supported = os.name == 'nt'

    def __init__(self, database):
        identity = os.path.normcase(str(database.resolve())).encode('utf-8')
        self.target = 'SessionAtlas/Decisions/' + hashlib.sha256(identity).hexdigest()

    def _api(self):
        if not self.supported:
            raise CredentialUnavailable('Secure credential storage unavailable')

        class Credential(ctypes.Structure):
            _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
                        ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
                        ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
                        ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', wintypes.DWORD),
                        ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                        ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]

        api = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(Credential))]
        api.CredReadW.restype = wintypes.BOOL
        api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        return api, Credential

    def read(self, *, presence_only=False):
        if not self.supported:
            return False if presence_only else None
        api, credential = self._api()
        pointer = ctypes.POINTER(credential)()
        if not api.CredReadW(self.target, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:
                return False if presence_only else None
            raise CredentialUnavailable('Credential store unavailable')
        try:
            if presence_only:
                return bool(pointer.contents.CredentialBlobSize)
            return ctypes.string_at(pointer.contents.CredentialBlob,
                                    pointer.contents.CredentialBlobSize).decode('utf-8')
        finally:
            api.CredFree(pointer)

    def write(self, key):
        api, credential = self._api()
        encoded = key.encode('utf-8')
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        value = credential(Type=1, TargetName=self.target, Comment='Session Atlas Decisions API',
                           CredentialBlobSize=len(encoded), CredentialBlob=blob,
                           Persist=2, UserName='Session Atlas')
        try:
            if not api.CredWriteW(ctypes.byref(value), 0):
                raise CredentialUnavailable('Credential could not be saved')
        finally:
            ctypes.memset(blob, 0, len(encoded))

    def delete(self):
        api, _ = self._api()
        if not api.CredDeleteW(self.target, 1, 0) and ctypes.get_last_error() != 1168:
            raise CredentialUnavailable('Credential could not be deleted')
