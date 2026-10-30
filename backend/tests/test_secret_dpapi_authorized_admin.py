import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from uuid import uuid4
from dwh_api.secret_store import SecretStore
from dwh_api.secret_create_coordinator import Request
from dwh_api.secret_authorized_admin_create import authorized_admin_create, AdminAuthorizationStopped
from dwh_api.secret_dpapi_crypto_provider import load_crypto_from_dpapi_file, DpapiKeyUnavailable

class GateTests(unittest.TestCase):
    def setUp(self):
        self.req=Request(str(uuid4()), 1, str(uuid4()))
        self.calls=[]
        self.allowed=SimpleNamespace(authenticated=True,
            user=SimpleNamespace(user_id=7,is_system=False,must_change_password=False),
            permissions=[SimpleNamespace(permission_code='existing.approved.create')])
    def factory(self):
        self.calls.append('factory')
        return SimpleNamespace(create=lambda req:self.calls.append('create') or 'receipt')
    def invoke(self, state=None, guard=None, permission='existing.approved.create'):
        return authorized_admin_create(request=self.req, trusted_auth_state=state or self.allowed,
            create_permission_code=permission, resource_guard=guard or (lambda r: True),
            coordinator_factory=self.factory)
    def test_authorized_call(self):
        self.assertEqual(self.invoke(),'receipt');self.assertEqual(self.calls,['factory','create'])
    def test_unauthed_prevents_factory(self):
        self.allowed.authenticated=False
        with self.assertRaises(AdminAuthorizationStopped):self.invoke()
        self.assertEqual(self.calls,[])
    def test_wrong_permission_prevents_factory(self):
        with self.assertRaises(AdminAuthorizationStopped):self.invoke(permission='other')
        self.assertEqual(self.calls,[])
    def test_resource_denied_prevents_factory(self):
        with self.assertRaises(AdminAuthorizationStopped):self.invoke(guard=lambda r:False)
        self.assertEqual(self.calls,[])
    def test_resource_guard_exception_fails_closed(self):
        def broken(r):raise RuntimeError('sensitive')
        with self.assertRaises(AdminAuthorizationStopped) as cm:self.invoke(guard=broken)
        self.assertNotIn('sensitive',str(cm.exception));self.assertEqual(self.calls,[])
    def test_system_identity_denied(self):
        self.allowed.user.is_system=True
        with self.assertRaises(AdminAuthorizationStopped):self.invoke()
        self.assertEqual(self.calls,[])
    def test_invalid_request_denied_before_factory(self):
        with self.assertRaises(AdminAuthorizationStopped):
            authorized_admin_create(request=None, trusted_auth_state=self.allowed,
            create_permission_code='existing.approved.create', resource_guard=lambda x:True,
            coordinator_factory=self.factory)
        self.assertEqual(self.calls,[])

class DpapiTests(unittest.TestCase):
    def test_invalid_path(self):
        with self.assertRaises(DpapiKeyUnavailable):load_crypto_from_dpapi_file('x')
    def test_unavailable_file(self):
        with self.assertRaises(DpapiKeyUnavailable):load_crypto_from_dpapi_file(Path('/definitely/not/a/file'))
    def test_valid_mock_dpapi_loads_existing_crypto(self):
        with tempfile.TemporaryDirectory() as temp:
            f=Path(temp)/'protected.dpapi';f.write_bytes(b'protected-blob-content')
            with patch('dwh_api.secret_dpapi_crypto_provider._win_unprotect',return_value=b'x'*32):
                c=load_crypto_from_dpapi_file(f)
            self.assertIsInstance(c,SecretStore)
            sid=str(uuid4());self.assertEqual(c.decrypt(secret_ref=sid,envelope=c.encrypt(secret_ref=sid,value='synthetic')),'synthetic')
    def test_failed_dpapi_no_secret_exposure(self):
        with tempfile.TemporaryDirectory() as temp:
            f=Path(temp)/'protected.dpapi';f.write_bytes(b'protected-blob-content')
            with patch('dwh_api.secret_dpapi_crypto_provider._win_unprotect',side_effect=RuntimeError('sensitive')):
                with self.assertRaises(DpapiKeyUnavailable) as cm:load_crypto_from_dpapi_file(f)
            self.assertNotIn('sensitive',str(cm.exception))
    def test_bad_key_length_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            f=Path(temp)/'protected.dpapi';f.write_bytes(b'protected-blob-content')
            with patch('dwh_api.secret_dpapi_crypto_provider._win_unprotect',return_value=b'x'*16):
                with self.assertRaises(DpapiKeyUnavailable):load_crypto_from_dpapi_file(f)
if __name__=='__main__': unittest.main()
