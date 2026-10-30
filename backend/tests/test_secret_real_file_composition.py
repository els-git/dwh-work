"""Real AES-256-GCM and filesystem integration; database remains a mock.
No live credentials, DB writes, API/ETL integration or claims of crash durability.
"""
import hashlib
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from dwh_api.secret_store import SecretStore
from dwh_api.secret_file_store import EncryptedFileSecretStore
from dwh_api.secret_create_coordinator import Request, CoordinationStopped
from dwh_api.secret_synthetic_success import compose_synthetic_create

SYNTHETIC = 'DWH-I02-03-ONLY-SYNTHETIC-PAYLOAD-987'

class DatabaseStub:
    def __init__(self): self.phases=[]; self.fail=None; self.digests=[]
    def permitted_attempt(self,op): self._phase('ATTEMPT')
    def pending(self,op): self._phase('PENDING')
    def failed_outcome(self,op): self._phase('FAILED')
    def successful_outcome(self,op,digest):
        self.digests.append(digest); self._phase('OUTCOME')
    def activate_verified(self,op,digest,*,file_is_verified):
        if file_is_verified is not True: raise ValueError('no evidence')
        self.digests.append(digest);self._phase('ACTIVE')
    def _phase(self,phase):
        self.phases.append(phase)
        if self.fail==phase: raise RuntimeError('hidden sensitive details')

class RealEncryptedFileIntegration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir=Path(self.temp.name)
        self.key=b'\x12'*32
        self.crypto=SecretStore(self.key)
        self.fs=EncryptedFileSecretStore(crypto=self.crypto,directory=self.dir)
        self.db=DatabaseStub()
        self.request=Request(str(uuid4()),1,str(uuid4()))
    def create(self):
        return compose_synthetic_create(self.db,self.db,self.fs,self.dir,lambda:SYNTHETIC).create(self.request)
    def file(self):return self.dir/(self.request.secret_ref+'.bin')
    def test_real_encryption_complete_order_and_readback(self):
        result=self.create()
        self.assertEqual(result.phase,'ACTIVE')
        self.assertEqual(self.db.phases,['ATTEMPT','PENDING','OUTCOME','ACTIVE'])
        data=self.file().read_bytes()
        self.assertNotIn(SYNTHETIC.encode(),data)
        self.assertEqual(self.db.digests,[hashlib.sha256(data).hexdigest()]*2)
        self.assertEqual(self.fs.read(secret_ref=self.request.secret_ref),SYNTHETIC)
    def test_ciphertext_is_different_for_new_reference(self):
        self.create()
        c1=self.file().read_bytes()
        ref2=str(uuid4());self.fs.create(secret_ref=ref2,value=SYNTHETIC)
        self.assertNotEqual(c1,(self.dir/(ref2+'.bin')).read_bytes())
    def test_duplicate_reference_rejected(self):
        self.create()
        first=self.file().read_bytes()
        with self.assertRaises(CoordinationStopped):self.create()
        self.assertEqual(first,self.file().read_bytes())
        self.assertEqual(self.db.phases[-1],'FAILED')
    def test_missing_directory_fails_closed(self):
        other=self.dir/'missing'
        with self.assertRaises(Exception):EncryptedFileSecretStore(crypto=self.crypto,directory=other)
    def test_attempt_failure_no_file(self):
        self.db.fail='ATTEMPT'
        with self.assertRaises(CoordinationStopped):self.create()
        self.assertFalse(self.file().exists())
        self.assertEqual(self.db.phases,['ATTEMPT'])
    def test_pending_failure_no_file(self):
        self.db.fail='PENDING'
        with self.assertRaises(CoordinationStopped):self.create()
        self.assertFalse(self.file().exists())
        self.assertNotIn('ACTIVE',self.db.phases)
    def test_outcome_failure_keeps_unpublished_file(self):
        self.db.fail='OUTCOME'
        with self.assertRaises(CoordinationStopped):self.create()
        self.assertTrue(self.file().exists())
        self.assertNotIn('ACTIVE',self.db.phases)
    def test_activation_failure_not_success(self):
        self.db.fail='ACTIVE'
        with self.assertRaises(CoordinationStopped):self.create()
        self.assertTrue(self.file().exists())
        self.assertEqual(self.db.phases[-1],'ACTIVE')
    def test_file_tampering_blocks_read(self):
        self.create()
        data=bytearray(self.file().read_bytes())
        data[-1]^=1
        self.file().write_bytes(data)
        with self.assertRaises(Exception):self.fs.read(secret_ref=self.request.secret_ref)
    def test_wrong_master_key_blocks_read(self):
        self.create()
        different=EncryptedFileSecretStore(crypto=SecretStore(b'\x34'*32),directory=self.dir)
        with self.assertRaises(Exception):different.read(secret_ref=self.request.secret_ref)
    def test_no_plaintext_in_exception(self):
        self.db.fail='ATTEMPT'
        try:self.create()
        except CoordinationStopped as e:
            self.assertNotIn(SYNTHETIC,str(e));self.assertNotIn('hidden',str(e))
    def test_no_pending_files_after_success(self):
        self.create()
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),[self.request.secret_ref+'.bin'])

if __name__=='__main__':unittest.main()
