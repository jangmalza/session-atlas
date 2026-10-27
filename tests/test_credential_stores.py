"""Cross-platform Decisions key storage. Uses an in-memory fake keyring; no real secrets."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from analysis_control import AnalysisControl, ControlError
from analysis_credentials import CredentialUnavailable, KeyringCredentials, WindowsCredentials, credential_store

SECRET = 'synthetic-keyring-key-never-use-for-real-requests'


class FakeKeyringErrors:
    class KeyringError(Exception):
        pass

    class PasswordDeleteError(KeyringError):
        pass

    class NoKeyringError(KeyringError):
        pass


class FakeBackend:
    def __init__(self, priority):
        self.priority = priority


class FakeKeyring:
    """Mimics the keyring module surface the store relies on."""

    errors = FakeKeyringErrors

    def __init__(self, priority=5, broken=False):
        self.store, self.backend, self.broken = {}, FakeBackend(priority), broken

    def get_keyring(self):
        return self.backend

    def get_password(self, service, username):
        if self.broken:
            raise self.errors.NoKeyringError('synthetic backend failure')
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        if self.broken:
            raise self.errors.KeyringError('synthetic backend failure')
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise self.errors.PasswordDeleteError('missing')
        del self.store[(service, username)]


class KeyringStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'synthetic.db'
        self.keyring = FakeKeyring()
        self.store = KeyringCredentials(self.database, module=self.keyring)

    def test_roundtrip_presence_and_delete(self):
        self.assertTrue(self.store.supported)
        self.assertIsNone(self.store.unavailable_reason)
        self.assertFalse(self.store.read(presence_only=True))
        self.assertIsNone(self.store.read())
        self.store.write(SECRET)
        self.assertTrue(self.store.read(presence_only=True))
        self.assertEqual(self.store.read(), SECRET)
        self.assertEqual(KeyringCredentials(self.database, module=self.keyring).read(), SECRET)
        self.store.delete()
        self.assertFalse(self.store.read(presence_only=True))
        self.store.delete()  # deleting an absent key is not an error, matching the Windows store

    def test_entries_are_scoped_per_database(self):
        other = KeyringCredentials(Path(self.temp.name) / 'other.db', module=self.keyring)
        self.store.write(SECRET)
        self.assertIsNone(other.read())
        self.assertFalse(other.read(presence_only=True))
        [(service, username)] = list(self.keyring.store)
        self.assertEqual(service, 'SessionAtlas/Decisions')
        self.assertNotIn(str(self.database), username)
        self.assertNotIn('synthetic', username)

    def test_fail_backend_is_reported_not_raised_on_read(self):
        store = KeyringCredentials(self.database, module=FakeKeyring(priority=0))
        self.assertFalse(store.supported)
        self.assertEqual(store.unavailable_reason, 'keyring_backend_missing')
        self.assertIsNone(store.read())
        self.assertFalse(store.read(presence_only=True))
        with self.assertRaises(CredentialUnavailable):
            store.write(SECRET)
        with self.assertRaises(CredentialUnavailable):
            store.delete()

    def test_missing_package_is_reported(self):
        with patch.dict(sys.modules, {'keyring': None}):
            store = KeyringCredentials(self.database)
            self.assertFalse(store.supported)
            self.assertEqual(store.unavailable_reason, 'keyring_missing')
            self.assertIsNone(store.read())
            with self.assertRaises(CredentialUnavailable):
                store.write(SECRET)

    def test_backend_failures_become_credential_unavailable(self):
        store = KeyringCredentials(self.database, module=FakeKeyring(broken=True))
        self.assertTrue(store.supported)
        with self.assertRaises(CredentialUnavailable):
            store.read()
        with self.assertRaises(CredentialUnavailable):
            store.write(SECRET)

    def test_factory_picks_platform_store(self):
        self.assertIsInstance(credential_store(self.database, platform='nt'), WindowsCredentials)
        self.assertIsInstance(credential_store(self.database, platform='posix'), KeyringCredentials)
        self.assertIsInstance(credential_store(self.database, platform='darwin'), KeyringCredentials)


class ControllerKeyStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'OPENAI_API_KEY': ''}).start()

    def controller(self, store):
        return AnalysisControl(self.root / 'collection.db', self.root / 'home', credentials=store)

    def test_keyring_store_is_reported_as_key_source(self):
        keyring = FakeKeyring()
        controller = self.controller(KeyringCredentials(self.root / 'collection.db', module=keyring))
        before = controller.settings()
        self.assertEqual(
            (before['keySource'], before['keyStorageAvailable'], before['keyStorageReason']), ('none', True, None)
        )
        after = controller.edit_settings({'action': 'save_key', 'key': SECRET})
        self.assertEqual((after['keyRegistered'], after['keySource']), (True, 'keyring'))
        self.assertNotIn(SECRET, str(after))
        self.assertEqual(controller.edit_settings({'action': 'delete_key'})['keySource'], 'none')
        self.assertEqual(keyring.store, {})

    def test_unavailable_store_reports_reason_and_rejects_saves(self):
        with patch.dict(sys.modules, {'keyring': None}):
            controller = self.controller(KeyringCredentials(self.root / 'collection.db'))
            value = controller.settings()
            self.assertEqual(
                (value['keyStorageAvailable'], value['keyStorageReason'], value['keySource']),
                (False, 'keyring_missing', 'none'),
            )
            with self.assertRaises(ControlError) as raised:
                controller.edit_settings({'action': 'save_key', 'key': SECRET})
            self.assertEqual(raised.exception.code, 'key_storage_unavailable')
            with patch.dict(os.environ, {'OPENAI_API_KEY': SECRET}):
                fallback = controller.settings()
            self.assertEqual((fallback['keyRegistered'], fallback['keySource']), (True, 'environment'))
            self.assertNotIn(SECRET, str(fallback))


def _real_keyring_usable():
    try:
        import keyring

        return getattr(keyring.get_keyring(), 'priority', 0) > 0
    except Exception:
        return False


@unittest.skipUnless(_real_keyring_usable(), 'keyring with a usable OS backend')
class RealKeyringIntegrationTests(unittest.TestCase):
    def test_isolated_roundtrip_and_delete(self):
        with tempfile.TemporaryDirectory() as name:
            store = KeyringCredentials(Path(name) / 'synthetic.db')
            try:
                store.write(SECRET)
                self.assertTrue(store.read(presence_only=True))
                self.assertEqual(store.read(), SECRET)
                self.assertEqual(KeyringCredentials(Path(name) / 'synthetic.db').read(), SECRET)
            finally:
                store.delete()
            self.assertFalse(store.read(presence_only=True))


if __name__ == '__main__':
    unittest.main()
