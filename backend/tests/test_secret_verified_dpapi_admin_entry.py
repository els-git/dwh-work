import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from dwh_api.secret_create_coordinator import Request
from dwh_api.secret_verified_dpapi_admin_entry import (
    create_verified_synthetic_secret, VerifiedAdminCreateStopped)

REQ = Request('00000000-0000-4000-8000-000000000001', 1,
              '00000000-0000-4000-8000-000000000002')


def deps():
    return dict(session_token='synthetic-session', request=REQ,
                readonly_connection=lambda: None,
                admin_connections=SimpleNamespace(writer=lambda: None, reader=lambda: None),
                dpapi_key_file=Path('/synthetic/key'),
                ciphertext_directory=Path('/synthetic/files'),
                synthetic_value_supplier=lambda: 'synthetic-only')


class Tests(unittest.TestCase):
    def test_invalid_admin_provider_fails_before_session(self):
        args = deps()
        args['admin_connections'] = object()
        with patch('dwh_api.secret_verified_dpapi_admin_entry.verified_session_create') as verify:
            with self.assertRaises(VerifiedAdminCreateStopped):
                create_verified_synthetic_secret(**args)
            verify.assert_not_called()

    def test_no_auth_state_parameter_or_loader_override(self):
        args = deps()
        args['trusted_auth_state'] = object()
        with self.assertRaises(TypeError):
            create_verified_synthetic_secret(**args)
        args = deps()
        args['session_loader'] = lambda _: object()
        with self.assertRaises(TypeError):
            create_verified_synthetic_secret(**args)

    def test_authorization_failure_prevents_dpapi_and_admin(self):
        with patch('dwh_api.secret_verified_dpapi_admin_entry.verified_session_create',
                   side_effect=ValueError('denied')):
            with patch('dwh_api.secret_verified_dpapi_admin_entry.load_crypto_from_dpapi_file') as key:
                with self.assertRaises(ValueError):
                    create_verified_synthetic_secret(**deps())
                key.assert_not_called()

    def test_verified_path_calls_real_coordinator_create(self):
        events = []
        class FakeCoordinator:
            def create(self, request):
                events.append(('create', request))
                return 'receipt'
        def gate(**kwargs):
            events.append('gate')
            return kwargs['execute_authorized_create'](object())
        with patch('dwh_api.secret_verified_dpapi_admin_entry.verified_session_create', side_effect=gate), \
             patch('dwh_api.secret_verified_dpapi_admin_entry.load_crypto_from_dpapi_file', side_effect=lambda _:events.append('key') or object()), \
             patch('dwh_api.secret_verified_dpapi_admin_entry.EncryptedFileSecretStore', side_effect=lambda **_:events.append('store') or object()), \
             patch('dwh_api.secret_verified_dpapi_admin_entry.compose_admin_synthetic_create', side_effect=lambda **_:events.append('compose') or FakeCoordinator()):
            self.assertEqual(create_verified_synthetic_secret(**deps()), 'receipt')
        self.assertEqual(events, ['gate','key','store','compose',('create',REQ)])

    def test_dpapi_failure_blocks_coordinator(self):
        def gate(**kwargs):
            return kwargs['execute_authorized_create'](object())
        with patch('dwh_api.secret_verified_dpapi_admin_entry.verified_session_create', side_effect=gate), \
             patch('dwh_api.secret_verified_dpapi_admin_entry.load_crypto_from_dpapi_file', side_effect=RuntimeError('secret')), \
             patch('dwh_api.secret_verified_dpapi_admin_entry.compose_admin_synthetic_create') as compose:
            with self.assertRaises(VerifiedAdminCreateStopped) as context:
                create_verified_synthetic_secret(**deps())
            self.assertNotIn('secret',str(context.exception))
            compose.assert_not_called()

    def test_write_failure_surfaces_for_reconciliation(self):
        def gate(**kwargs):
            return kwargs['execute_authorized_create'](object())
        class Failure:
            def create(self, request):
                raise RuntimeError('partial COMMIT; reconciliation necessary')
        with patch('dwh_api.secret_verified_dpapi_admin_entry.verified_session_create', side_effect=gate), \
             patch('dwh_api.secret_verified_dpapi_admin_entry.load_crypto_from_dpapi_file', return_value=object()), \
             patch('dwh_api.secret_verified_dpapi_admin_entry.EncryptedFileSecretStore', return_value=object()), \
             patch('dwh_api.secret_verified_dpapi_admin_entry.compose_admin_synthetic_create', return_value=Failure()):
            with self.assertRaisesRegex(RuntimeError,'reconciliation necessary'):
                create_verified_synthetic_secret(**deps())

if __name__ == '__main__':
    unittest.main()
