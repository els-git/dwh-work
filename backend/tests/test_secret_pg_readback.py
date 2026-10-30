import unittest
from dwh_api.secret_pg_readback import verify_event, verify_binding, ReadbackDenied
R='123e4567-e89b-42d3-a456-426614174aa1'
O='223e4567-e89b-42d3-a456-426614174aa1'
class Cursor:
    def __init__(self,rows,read_only='on',fault=False):
        self.rows=list(rows);self.read_only=read_only;self.fault=fault;self.statement=None
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def execute(self,sql,params=None):
        if self.fault:raise ValueError('sensitive database exception')
        self.statement=(sql,params)
        self.current=[(self.read_only,)] if params is None else list(self.rows)
    def fetchone(self):return self.current.pop(0) if self.current else None
class Connection:
    def __init__(self,rows,read_only='on',fault=False):
        self.cur=Cursor(rows,read_only,fault);self.closed=False;self.rolled=False
    def cursor(self):return self.cur
    def rollback(self):self.rolled=True
    def close(self):self.closed=True
class Tests(unittest.TestCase):
    def factory(self,rows,**kwargs):
        c=Connection(rows,**kwargs);return c,lambda:c
    def test_event_readback(self):
        c,f=self.factory([(O,O,1,'ATTEMPT','PERMITTED_ATTEMPT')]);res=verify_event(f,O,1,'ATTEMPT','PERMITTED_ATTEMPT')
        self.assertEqual(res.kind,'ATTEMPT');self.assertTrue(c.rolled and c.closed)
        self.assertEqual(c.cur.statement[1],(O,'ATTEMPT',1))
    def test_binding_readback(self):
        c,f=self.factory([(R,O,1,'PENDING',None)]);res=verify_binding(f,R,O,1,'PENDING')
        self.assertEqual(res.state_or_code,'PENDING');self.assertTrue(c.rolled and c.closed)
    def test_active_digest(self):
        d='a'*64;c,f=self.factory([(R,O,1,'ACTIVE',d)])
        self.assertEqual(verify_binding(f,R,O,1,'ACTIVE',d).ciphertext_sha256,d)
    def test_missing_event(self):
        c,f=self.factory([])
        with self.assertRaises(ReadbackDenied):verify_event(f,O,1,'OUTCOME','SUCCEEDED')
        self.assertTrue(c.closed)
    def test_mismatched_event(self):
        c,f=self.factory([(O,O,1,'ATTEMPT','DENIED_ATTEMPT')])
        with self.assertRaises(ReadbackDenied):verify_event(f,O,1,'ATTEMPT','PERMITTED_ATTEMPT')
    def test_duplicate_event(self):
        c,f=self.factory([(O,O,1,'ATTEMPT','PERMITTED_ATTEMPT')]*2)
        with self.assertRaises(ReadbackDenied):verify_event(f,O,1,'ATTEMPT','PERMITTED_ATTEMPT')
    def test_wrong_owner(self):
        c,f=self.factory([(R,O,5,'PENDING',None)])
        with self.assertRaises(ReadbackDenied):verify_binding(f,R,O,1,'PENDING')
    def test_wrong_digest(self):
        c,f=self.factory([(R,O,1,'ACTIVE','b'*64)])
        with self.assertRaises(ReadbackDenied):verify_binding(f,R,O,1,'ACTIVE','a'*64)
    def test_non_readonly_denied(self):
        c,f=self.factory([],read_only='off')
        with self.assertRaises(ReadbackDenied):verify_event(f,O,1,'ATTEMPT','PERMITTED_ATTEMPT')
        self.assertTrue(c.rolled and c.closed)
    def test_sql_exception_redacted(self):
        c,f=self.factory([],fault=True)
        with self.assertRaises(ReadbackDenied) as cm:verify_event(f,O,1,'ATTEMPT','PERMITTED_ATTEMPT')
        self.assertNotIn('sensitive',str(cm.exception));self.assertTrue(c.closed)
    def test_bad_uuid_short_circuits(self):
        with self.assertRaises(ReadbackDenied):verify_event(lambda: self.fail('connected'),'bad',1,'ATTEMPT','PERMITTED_ATTEMPT')
    def test_bad_phase_denied(self):
        with self.assertRaises(ReadbackDenied):verify_event(lambda: self.fail('connected'),O,1,'INVALID','SUCCEEDED')
    def test_bool_source_denied(self):
        with self.assertRaises(ReadbackDenied):verify_event(lambda: self.fail('connected'),O,True,'ATTEMPT','PERMITTED_ATTEMPT')
if __name__=='__main__':unittest.main()
