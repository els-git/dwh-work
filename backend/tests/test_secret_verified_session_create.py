import hashlib
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from dwh_api.secret_create_coordinator import Request
from dwh_api.secret_verified_session_create import verified_session_create, SessionCreateStopped

REQ = Request('synthetic-ref', 1, 'synthetic-op')

class Tests(unittest.TestCase):
    def test_positive_fresh_session_and_scope(self):
        recorded=[]
        state=object()
        def loader(digest): recorded.append(('hash',digest)); return state
        def create(s): recorded.append(('create',s)); return 'done'
        with patch('dwh_api.secret_verified_session_create.authorize_source_secret_create',return_value=True) as auth:
            self.assertEqual(verified_session_create(raw_session_token='synthetic-token',request=REQ,
                readonly_connection=lambda:None,execute_authorized_create=create,session_loader=loader),'done')
        self.assertEqual(recorded[0],('hash',hashlib.sha256(b'synthetic-token').hexdigest()))
        self.assertEqual(recorded[1],('create',state))
        auth.assert_called_once()
    def test_missing_session_blocks_executor(self):
        calls=[]
        with self.assertRaises(SessionCreateStopped):
            verified_session_create(raw_session_token='synthetic',request=REQ,
                readonly_connection=lambda:None,execute_authorized_create=lambda s:calls.append(s),session_loader=lambda _:None)
        self.assertEqual(calls,[])
    def test_scope_denied_blocks_executor(self):
        calls=[]
        with patch('dwh_api.secret_verified_session_create.authorize_source_secret_create',return_value=False):
            with self.assertRaises(SessionCreateStopped):
                verified_session_create(raw_session_token='synthetic',request=REQ,
                    readonly_connection=lambda:None,execute_authorized_create=lambda s:calls.append(s),session_loader=lambda _:object())
        self.assertEqual(calls,[])
    def test_loader_error_redacted(self):
        def bad(_):raise RuntimeError('sensitive token')
        with self.assertRaises(SessionCreateStopped) as ctx:
            verified_session_create(raw_session_token='synthetic',request=REQ,
                readonly_connection=lambda:None,execute_authorized_create=lambda _:None,session_loader=bad)
        self.assertNotIn('sensitive',str(ctx.exception))
    def test_bad_input_prevents_loader(self):
        for token in ('', 'x'*513, None):
            with self.subTest(token=repr(token)[:10]):
                with self.assertRaises(SessionCreateStopped):
                    verified_session_create(raw_session_token=token,request=REQ,
                       readonly_connection=lambda:None,execute_authorized_create=lambda _:None,
                       session_loader=lambda _:self.fail('loader invoked'))
    def test_bad_source_fails_closed(self):
        with patch('dwh_api.secret_verified_session_create.authorize_source_secret_create',return_value=False):
            with self.assertRaises(SessionCreateStopped):
                verified_session_create(raw_session_token='synthetic',request=Request('ref',-1,'op'),
                    readonly_connection=lambda:None,execute_authorized_create=lambda _:self.fail('create'),
                    session_loader=lambda _:object())
    def test_executor_error_propagates_for_reconciliation(self):
        with patch('dwh_api.secret_verified_session_create.authorize_source_secret_create',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'reconcile'):
                verified_session_create(raw_session_token='synthetic',request=REQ,
                    readonly_connection=lambda:None,execute_authorized_create=lambda _: (_ for _ in ()).throw(RuntimeError('reconcile')),
                    session_loader=lambda _:object())

if __name__=='__main__':unittest.main()
