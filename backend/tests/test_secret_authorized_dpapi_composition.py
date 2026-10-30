import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from pathlib import Path
from dwh_api.secret_create_coordinator import Request
from dwh_api.secret_authorized_dpapi_composition import authorized_dpapi_synthetic_create, AuthorizedCompositionStopped

class Tests(unittest.TestCase):
    def setUp(self):
        self.req = Request(str(uuid4()), 1, str(uuid4()))
        self.state = SimpleNamespace(authenticated=True,
            user=SimpleNamespace(user_id=7, is_system=False, must_change_password=False),
            permissions=[SimpleNamespace(permission_code='approved.create')])
        self.actions=[]
    def call(self, state=None, guard=None):
        return authorized_dpapi_synthetic_create(request=self.req,
            trusted_auth_state=self.state if state is None else state,
            create_permission_code='approved.create',
            resource_guard=(lambda resource: True) if guard is None else guard,
            connections=object(), dpapi_key_file=Path('/synthetic-only.dpapi'),
            ciphertext_directory=Path('/synthetic-only'), synthetic_value_supplier=lambda:'synthetic')
    def test_auth_denial_does_not_touch_key_or_database(self):
        self.state.authenticated=False
        with patch('dwh_api.secret_authorized_dpapi_composition.load_crypto_from_dpapi_file') as key, patch('dwh_api.secret_authorized_dpapi_composition.compose_admin_synthetic_create') as compose:
            with self.assertRaises(AuthorizedCompositionStopped):self.call()
            key.assert_not_called();compose.assert_not_called()
    def test_resource_denial_precedes_dpapi(self):
        with patch('dwh_api.secret_authorized_dpapi_composition.load_crypto_from_dpapi_file') as key:
            with self.assertRaises(AuthorizedCompositionStopped):self.call(guard=lambda r:False)
            key.assert_not_called()
    def test_key_failure_prevents_composition(self):
        with patch('dwh_api.secret_authorized_dpapi_composition.load_crypto_from_dpapi_file',side_effect=RuntimeError('secret-key')) as key, patch('dwh_api.secret_authorized_dpapi_composition.compose_admin_synthetic_create') as compose:
            with self.assertRaises(AuthorizedCompositionStopped) as cm:self.call()
            self.assertNotIn('secret-key',str(cm.exception));key.assert_called_once();compose.assert_not_called()
    def test_authorized_sequence(self):
        from dwh_api.secret_store import SecretStore
        crypto=SecretStore(b'x'*32)
        with patch('dwh_api.secret_authorized_dpapi_composition.load_crypto_from_dpapi_file',return_value=crypto) as key, patch('dwh_api.secret_authorized_dpapi_composition.EncryptedFileSecretStore') as file_store, patch('dwh_api.secret_authorized_dpapi_composition.compose_admin_synthetic_create') as compose:
            compose.return_value.create.side_effect=lambda r:self.actions.append(r) or 'ACTIVE-RECEIPT'
            self.assertEqual(self.call(),'ACTIVE-RECEIPT')
            self.assertEqual(self.actions,[self.req]);key.assert_called_once();file_store.assert_called_once();compose.assert_called_once()
    def test_create_error_sanitized(self):
        from dwh_api.secret_store import SecretStore
        with patch('dwh_api.secret_authorized_dpapi_composition.load_crypto_from_dpapi_file',return_value=SecretStore(b'x'*32)),patch('dwh_api.secret_authorized_dpapi_composition.EncryptedFileSecretStore'),patch('dwh_api.secret_authorized_dpapi_composition.compose_admin_synthetic_create') as compose:
            compose.return_value.create.side_effect=RuntimeError('sensitive')
            with self.assertRaises(AuthorizedCompositionStopped) as cm:self.call()
            self.assertNotIn('sensitive',str(cm.exception))
    def test_no_dwh_context_denied(self):
        with patch('dwh_api.secret_authorized_dpapi_composition.load_crypto_from_dpapi_file') as key:
            with self.assertRaises(AuthorizedCompositionStopped):self.call(state=object())
            key.assert_not_called()
if __name__=='__main__': unittest.main()
