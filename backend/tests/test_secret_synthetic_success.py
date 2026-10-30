import hashlib
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from dwh_api.secret_create_coordinator import Request, CoordinationStopped
from dwh_api.secret_synthetic_success import compose_synthetic_create, SyntheticFileStep, PgSuccessBridge

class FileStore:
    def __init__(self,path): self.path=path;self.fail=False;self.events=[]
    def create(self,*,secret_ref,value):
        self.events.append('FILE')
        if self.fail: raise RuntimeError('hidden synthetic plaintext')
        (self.path/(secret_ref+'.bin')).write_bytes(b'encrypted:synthetic-only')

class Adapter:
    def __init__(self,events): self.events=events;self.fail=None
    def _do(self,phase,op):
        self.events.append(phase)
        if self.fail==phase: raise ValueError('secret hidden')
    def permitted_attempt(self,op): self._do('ATTEMPT',op)
    def pending(self,op): self._do('PENDING',op)
    def failed_outcome(self,op): self._do('FAILED',op)

class Success:
    def __init__(self,events): self.events=events;self.fail=None;self.digests=[]
    def successful_outcome(self,op,digest):
        self.events.append('OUTCOME');self.digests.append(digest)
        if self.fail=='OUTCOME': raise ValueError('secret hidden')
    def activate_verified(self,op,digest,*,file_is_verified):
        self.events.append('ACTIVE');self.digests.append(digest)
        if file_is_verified is not True or self.fail=='ACTIVE': raise ValueError('secret hidden')

class Integration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.events=[];self.pg=Adapter(self.events);self.success=Success(self.events)
        self.store=FileStore(self.path);self.store.events=self.events
        self.req=Request(str(uuid4()),1,str(uuid4()))
    def subject(self):return compose_synthetic_create(self.pg,self.success,self.store,self.path,lambda:'synthetic-only')
    def test_successful_composition(self):
        r=self.subject().create(self.req)
        self.assertEqual(r.phase,'ACTIVE')
        self.assertEqual(self.events,['ATTEMPT','PENDING','FILE','OUTCOME','ACTIVE'])
        self.assertEqual(self.success.digests,[hashlib.sha256(b'encrypted:synthetic-only').hexdigest()]*2)
    def test_attempt_failure_blocks_file(self):
        self.pg.fail='ATTEMPT'
        with self.assertRaises(CoordinationStopped):self.subject().create(self.req)
        self.assertEqual(self.events,['ATTEMPT'])
    def test_pending_failure_records_failed(self):
        self.pg.fail='PENDING'
        with self.assertRaises(CoordinationStopped):self.subject().create(self.req)
        self.assertEqual(self.events,['ATTEMPT','PENDING','FAILED'])
    def test_file_failure_blocks_success(self):
        self.store.fail=True
        with self.assertRaises(CoordinationStopped):self.subject().create(self.req)
        self.assertEqual(self.events,['ATTEMPT','PENDING','FILE','FAILED'])
    def test_outcome_failure_blocks_activation(self):
        self.success.fail='OUTCOME'
        with self.assertRaises(CoordinationStopped):self.subject().create(self.req)
        self.assertEqual(self.events,['ATTEMPT','PENDING','FILE','OUTCOME','FAILED'])
    def test_activation_failure_no_failed_outcome(self):
        self.success.fail='ACTIVE'
        with self.assertRaises(CoordinationStopped):self.subject().create(self.req)
        self.assertEqual(self.events,['ATTEMPT','PENDING','FILE','OUTCOME','ACTIVE'])
    def test_missing_file_denied(self):
        class Missing(FileStore):
            def create(self,*,secret_ref,value): self.events.append('FILE')
        self.store=Missing(self.path);self.store.events=self.events
        with self.assertRaises(CoordinationStopped):self.subject().create(self.req)
        self.assertEqual(self.events,['ATTEMPT','PENDING','FILE','FAILED'])
    def test_empty_ciphertext_denied(self):
        class Empty(FileStore):
            def create(self,*,secret_ref,value):
                self.events.append('FILE');(self.path/(secret_ref+'.bin')).write_bytes(b'')
        self.store=Empty(self.path);self.store.events=self.events
        with self.assertRaises(CoordinationStopped):self.subject().create(self.req)
    def test_sanitized_error(self):
        self.pg.fail='ATTEMPT'
        try:self.subject().create(self.req)
        except CoordinationStopped as e:self.assertNotIn('hidden',str(e))
    def test_bad_id_denied_before_fs(self):
        self.req=Request('wrong',1,str(uuid4()))
        with self.assertRaises(CoordinationStopped):self.subject().create(self.req)
        self.assertEqual(self.events,['ATTEMPT','PENDING','FAILED'])

if __name__=='__main__':unittest.main()
