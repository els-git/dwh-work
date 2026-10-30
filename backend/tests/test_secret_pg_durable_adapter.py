import unittest
from dwh_api.secret_pg_durable_adapter import PgDurableAdapter,DbOperation,PersistenceStopped

OP=DbOperation('faf89680-0a99-45f2-a7e7-63f04de3bbb2','60890348-eacf-4128-9f91-31092a5d7a96',1)

class Cursor:
    def __init__(self, connection): self.c=connection;self.out=None
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def execute(self,sql,args=None):
        self.c.sql.append((sql,args))
        if self.c.fail: raise RuntimeError('sensitive info never shown')
        if sql=='SHOW transaction_read_only': self.out=[(self.c.mode,)]
        elif 'current_database(), current_user' in sql:self.out=[(self.c.db,self.c.user)]
        elif sql=='SELECT current_database()':self.out=[(self.c.db,)]
        elif sql.startswith('SELECT count(*)'): self.out=self.c.rows
        else:self.out=[]
    def fetchone(self):return self.out[0]
    def fetchall(self):return self.out

class Conn:
    def __init__(self,mode,rows=None,db='dwh',user='dwh_admin',fail=False):
        self.mode=mode;self.rows=rows if rows is not None else [(1,)];self.db=db;self.user=user
        self.fail=fail;self.sql=[];self.commits=0;self.rollbacks=0;self.closed=False
    def cursor(self):return Cursor(self)
    def commit(self):self.commits+=1
    def rollback(self):self.rollbacks+=1
    def close(self):self.closed=True

class Tests(unittest.TestCase):
    def adapter(self,w=None,r=None):
        w=w or Conn('off');r=r or Conn('on');return PgDurableAdapter(lambda:w,lambda:r),w,r
    def test_three_steps_separate_commits_and_readbacks(self):
        for method in ('permitted_attempt','pending','failed_outcome'):
            a,w,r=self.adapter();getattr(a,method)(OP)
            self.assertEqual(w.commits,1);self.assertEqual(r.rollbacks,1)
            self.assertTrue(w.closed and r.closed)
            self.assertEqual(sum('SELECT count(*)' in s for s,_ in r.sql),1)
    def test_missing_readback_denied(self):
        a,w,r=self.adapter(r=Conn('on',rows=[(0,)]))
        with self.assertRaises(PersistenceStopped):a.pending(OP)
        self.assertEqual(w.commits,1)
    def test_duplicate_readback_denied(self):
        a,_,_=self.adapter(r=Conn('on',rows=[(2,)]))
        with self.assertRaises(PersistenceStopped):a.failed_outcome(OP)
    def test_nonreadonly_reader_denied(self):
        a,_,r=self.adapter(r=Conn('off'))
        with self.assertRaises(PersistenceStopped):a.permitted_attempt(OP)
        self.assertTrue(r.closed)
    def test_nonwritable_writer_denied_before_insert(self):
        a,w,_=self.adapter(w=Conn('on'))
        with self.assertRaises(PersistenceStopped):a.permitted_attempt(OP)
        self.assertFalse(any(s.startswith('INSERT') for s,_ in w.sql))
    def test_wrong_writer_role_denied(self):
        a,w,_=self.adapter(w=Conn('off',user='dwh_app'))
        with self.assertRaises(PersistenceStopped):a.pending(OP)
        self.assertFalse(any(s.startswith('INSERT') for s,_ in w.sql))
    def test_wrong_db_denied(self):
        a,_,_=self.adapter(r=Conn('on',db='other'))
        with self.assertRaises(PersistenceStopped):a.pending(OP)
    def test_write_failure_denied_and_rolled_back(self):
        a,w,_=self.adapter(w=Conn('off',fail=True))
        with self.assertRaises(PersistenceStopped):a.pending(OP)
        self.assertEqual(w.rollbacks,1)
        self.assertEqual(w.commits,0)
    def test_readback_failure_denied(self):
        a,_,_=self.adapter(r=Conn('on',fail=True))
        with self.assertRaises(PersistenceStopped):a.pending(OP)
    def test_no_secret_in_exceptions(self):
        a,_,_=self.adapter(w=Conn('off',fail=True))
        try:a.pending(OP)
        except PersistenceStopped as ex:
            self.assertNotIn(OP.secret_ref,str(ex));self.assertNotIn('sensitive info',str(ex))
    def test_invalid_ids_no_connections(self):
        calls=[]
        a=PgDurableAdapter(lambda:calls.append(1),lambda:calls.append(2))
        with self.assertRaises(PersistenceStopped):a.permitted_attempt(DbOperation('no','bad',1))
        self.assertEqual(calls,[])
    def test_parameterized_sql(self):
        a,w,_=self.adapter();a.pending(OP)
        inserts=[(s,args) for s,args in w.sql if s.startswith('INSERT')]
        self.assertEqual(len(inserts),1)
        self.assertIn('%s',inserts[0][0]);self.assertNotIn(OP.secret_ref,inserts[0][0])
    def test_no_active_write_path(self):
        a,_,_=self.adapter()
        self.assertFalse(hasattr(a,'activate'))

if __name__=='__main__':unittest.main()
