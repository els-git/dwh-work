import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dwh_api.secret_create_coordinator import Request, CoordinationStopped
from dwh_api.secret_admin_create_integration import _AdminBridge, _VerifiedFile, _op, AdminCreateStopped, compose_admin_synthetic_create

REQ=Request('60890348-eacf-4128-9f91-31092a5d7a96',1,'faf89680-0a99-45f2-a7e7-63f04de3bbb2')

class FakeFile:
    def __init__(self, folder): self._directory=Path(folder)

class FakeWriter:
    def __init__(self, store):self.store=store
    def create_and_verify(self, *, secret_ref, value):
        content=('ciphertext:'+value).encode()
        (self.store._directory/(secret_ref+'.bin')).write_bytes(content)
        return hashlib.sha256(content).hexdigest()

class Db:
    def __init__(self):self.calls=[];self.fail=None
    def _call(self, name):
        self.calls.append(name)
        if self.fail==name:raise RuntimeError('sensitive')
    def permitted_attempt(self, op):self._call('attempt')
    def pending(self,op):self._call('pending')
    def failed_outcome(self,op):self._call('failure')

class Success:
    def __init__(self, db):self.db=db
    def successful_outcome(self,op,d):self.db._call('outcome')
    def activate_verified(self,op,d,*,file_is_verified):
        assert file_is_verified is True
        self.db._call('active')

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=FakeFile(self.tmp.name)
        with patch('dwh_api.secret_admin_create_integration.EncryptedFileSecretStore', FakeFile), patch('dwh_api.secret_admin_create_integration.WindowsDurableFileCreate',FakeWriter):
            self.file=_VerifiedFile(self.store,lambda:'synthetic-only')
        self.db=Db();self.bridge=_AdminBridge(self.db,Success(self.db),self.file)

    def file_digest(self):
        return self.file.create_durable(REQ)

    def test_success_path(self):
        self.bridge.commit_attempt(REQ);self.bridge.commit_pending(REQ)
        d=self.file_digest();self.bridge.commit_outcome(REQ,d);self.bridge.activate(REQ,d)
        self.assertEqual(self.db.calls,['attempt','pending','outcome','active'])

    def test_no_digest_cannot_publish(self):
        with self.assertRaises(AdminCreateStopped):self.bridge.commit_outcome(REQ,'a'*64)
        self.assertEqual(self.db.calls,[])

    def test_file_deleted_blocks_active(self):
        d=self.file_digest();(self.store._directory/(REQ.secret_ref+'.bin')).unlink()
        with self.assertRaises(AdminCreateStopped):self.bridge.activate(REQ,d)
        self.assertNotIn('active',self.db.calls)

    def test_file_corrupted_blocks_active(self):
        d=self.file_digest();(self.store._directory/(REQ.secret_ref+'.bin')).write_bytes(b'altered')
        with self.assertRaises(AdminCreateStopped):self.bridge.activate(REQ,d)
        self.assertNotIn('active',self.db.calls)

    def test_wrong_digest_blocks_active(self):
        self.file_digest()
        with self.assertRaises(AdminCreateStopped):self.bridge.activate(REQ,'a'*64)

    def test_wrong_operation_blocks_active(self):
        d=self.file_digest()
        other=Request(REQ.secret_ref,REQ.source_system_id,'11111111-1111-4111-8111-111111111111')
        with self.assertRaises(AdminCreateStopped):self.bridge.activate(other,d)

    def test_duplicate_proof_denied(self):
        self.file_digest()
        with self.assertRaises(AdminCreateStopped):self.file_digest()

    def test_proof_one_time(self):
        d=self.file_digest();self.bridge.activate(REQ,d)
        with self.assertRaises(AdminCreateStopped):self.bridge.activate(REQ,d)

    def test_outcome_db_failure_no_active(self):
        d=self.file_digest();self.db.fail='outcome'
        with self.assertRaises(AdminCreateStopped):self.bridge.commit_outcome(REQ,d)
        self.assertNotIn('active',self.db.calls)

    def test_invalid_uuid(self):
        with self.assertRaises(AdminCreateStopped):_op(Request('bad',1,REQ.operation_id))

    def test_invalid_role_factory(self):
        with self.assertRaises(AdminCreateStopped):compose_admin_synthetic_create(connections=object(),encrypted_store=None,synthetic_value_supplier=None)

    def test_no_sensitive_error_leak(self):
        self.db.fail='attempt'
        try:self.bridge.commit_attempt(REQ)
        except AdminCreateStopped as e:self.assertNotIn('sensitive',str(e))
        else:self.fail('Expected stop')

if __name__=='__main__':unittest.main()
