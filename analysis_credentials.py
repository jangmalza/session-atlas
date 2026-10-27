"""Database-scoped secret storage for the Decisions key. No secret is written to app files.

Windows uses the Credential Manager through ctypes (standard library only). Other
platforms use the optional ``keyring`` package, which talks to the macOS Keychain,
Secret Service (GNOME Keyring, KWallet) or another installed backend. ``keyring`` is
imported lazily and only by the store itself, so collection and viewing never load it.
"""

import ctypes
import hashlib
import os

if os.name == 'nt':
    from ctypes import wintypes


class CredentialUnavailable(RuntimeError):
    pass


def _identity(database):
    """Stable per-database identity so two archives never share one key entry."""
    normalized = os.path.normcase(str(database.resolve())).encode('utf-8')
    return hashlib.sha256(normalized).hexdigest()


def credential_store(database, *, platform=os.name):
    """Return the secret store for this platform: Credential Manager on Windows, keyring elsewhere."""
    if platform == 'nt':
        return WindowsCredentials(database)
    return KeyringCredentials(database)


class WindowsCredentials:
    kind = 'windows'
    supported = os.name == 'nt'

    def __init__(self, database):
        self.target = 'SessionAtlas/Decisions/' + _identity(database)

    def _api(self):
        if not self.supported:
            raise CredentialUnavailable('Secure credential storage unavailable')

        class Credential(ctypes.Structure):
            _fields_ = [
                ('Flags', wintypes.DWORD),
                ('Type', wintypes.DWORD),
                ('TargetName', wintypes.LPWSTR),
                ('Comment', wintypes.LPWSTR),
                ('LastWritten', wintypes.FILETIME),
                ('CredentialBlobSize', wintypes.DWORD),
                ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
                ('Persist', wintypes.DWORD),
                ('AttributeCount', wintypes.DWORD),
                ('Attributes', ctypes.c_void_p),
                ('TargetAlias', wintypes.LPWSTR),
                ('UserName', wintypes.LPWSTR),
            ]

        api = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
        api.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(Credential)),
        ]
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
            return ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize).decode(
                'utf-8'
            )
        finally:
            api.CredFree(pointer)

    def write(self, key):
        api, credential = self._api()
        encoded = key.encode('utf-8')
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        value = credential(
            Type=1,
            TargetName=self.target,
            Comment='Session Atlas Decisions API',
            CredentialBlobSize=len(encoded),
            CredentialBlob=blob,
            Persist=2,
            UserName='Session Atlas',
        )
        try:
            if not api.CredWriteW(ctypes.byref(value), 0):
                raise CredentialUnavailable('Credential could not be saved')
        finally:
            ctypes.memset(blob, 0, len(encoded))

    def delete(self):
        api, _ = self._api()
        if not api.CredDeleteW(self.target, 1, 0) and ctypes.get_last_error() != 1168:
            raise CredentialUnavailable('Credential could not be deleted')

    @property
    def unavailable_reason(self):
        return None if self.supported else 'unsupported_platform'


class KeyringCredentials:
    """Store the key through ``keyring`` under a service name plus a per-database username."""

    kind = 'keyring'
    service = 'SessionAtlas/Decisions'

    def __init__(self, database, *, module=None):
        self.username = _identity(database)
        self._module = module
        self._resolved = False
        self._reason = None

    def _resolve(self):
        if self._resolved:
            return
        self._resolved = True
        if self._module is None:
            try:
                import keyring  # optional dependency; absent on stdlib-only installs
            except ImportError:
                self._reason = 'keyring_missing'
                return
            self._module = keyring
        try:
            backend = self._module.get_keyring()
            viable = getattr(backend, 'priority', 0) > 0
        except Exception:
            viable = False
        if not viable:
            # keyring's fail backend (priority 0) means no usable OS keychain was found.
            self._reason = 'keyring_backend_missing'

    @property
    def supported(self):
        self._resolve()
        return self._reason is None

    @property
    def unavailable_reason(self):
        self._resolve()
        return self._reason

    def _require(self):
        if not self.supported:
            raise CredentialUnavailable('Secure credential storage unavailable: ' + self._reason)
        return self._module

    def read(self, *, presence_only=False):
        if not self.supported:
            return False if presence_only else None
        try:
            value = self._module.get_password(self.service, self.username)
        except Exception:
            raise CredentialUnavailable('Credential store unavailable') from None
        if presence_only:
            return bool(value)
        return value or None

    def write(self, key):
        module = self._require()
        try:
            module.set_password(self.service, self.username, key)
        except Exception:
            raise CredentialUnavailable('Credential could not be saved') from None

    def delete(self):
        module = self._require()
        if not self.read(presence_only=True):
            return
        try:
            module.delete_password(self.service, self.username)
        except Exception:
            raise CredentialUnavailable('Credential could not be deleted') from None
