import unittest
from dwh_api.secret_pg_durable_adapter import PgDurableAdapter, DbOperation, PersistenceStopped
from dwh_api.secret_pg_success_extension import PgSuccessExtension

OP=DbOperation('faf89680-0a99-45f2-a7e7-63f04de3bbb2','60890348-eacf-4128-9f91-31092a5d7a96',1)
DIGEST='a'*64
class Cursor:
 def __init__(self,conn): self.c=conn;self.rows=[];self.rowcount=1
 def __enter__(self): return self
 def __exit__(self,*args):return False
 def execute(self,sql,args=None):
  self.c.statements.append((sql,args))
  if self.c.fail:raise RuntimeError('secret-value')
  if sql=='SHOW transaction_read_only':self.rows=[(self.c.mode,)]
  elif sql=='SELECT current_database(), current_user':self.rows=[(self.c.db,self.c.user)]
  elif sql=='SELECT current_database()':self.rows=[(self.c.db,)]
  elif sql.startswith('SELECT count(*)'):self.rows=[(self.c.count,)]
  elif sql.startswith('UPDATE'):self.rowcount=self.c.updated
  else:self.rows=[]
 def fetchone(self):return self.rows[0]
 def fetchall(self):return self.rows
class Conn:
 def __init__(self,mode,*,count=1,updated=1,fail=False,db='dwh',user='dwh_admin'):
  self.mode=mode;self.count=count;self.updated=updated;self.fail=fail;self.db=db;self.user=user
  self.statements=[];self.commits=0;self.rollbacks=0;self.closed=False
 def cursor(self):return Cursor(self)
 def commit(self):self.commits+=1
 def rollback(self):self.rollbacks+=1
 def close(self):self.closed=True
class TestSuccessExtension(unittest.TestCase):
 def setup(self,w=None,r=None):
  w=w or Conn('off');r=r or Conn('on');return PgSuccessExtension(PgDurableAdapter(lambda:w,lambda:r)),w,r
 def test_success_outcome(self):
  x,w,r=self.setup();x.successful_outcome(OP,DIGEST)
  self.assertEqual((w.commits,r.rollbacks),(1,1));self.assertTrue(w.closed and r.closed)
  self.assertIn('OUTCOME',str(w.statements));self.assertNotIn(OP.secret_ref,str(w.statements[2][0]))
 def test_activation(self):
  x,w,r=self.setup();x.activate_verified(OP,DIGEST,file_is_verified=True)
  self.assertEqual((w.commits,r.rollbacks),(1,1));self.assertTrue(w.closed and r.closed)
 def test_denied_without_file_receipt(self):
  for v in (False,None,'true',1):
   x,w,r=self.setup()
   with self.assertRaises(PersistenceStopped):x.activate_verified(OP,DIGEST,file_is_verified=v)
   self.assertEqual(w.commits,0)
 def test_bad_digest_no_connections(self):
  for v in ('','g'*64,'A'*64,'a'*63,None):
   x,w,r=self.setup()
   with self.assertRaises(PersistenceStopped):x.successful_outcome(OP,v)
   self.assertEqual(w.statements,[])
 def test_zero_update_rolls_back(self):
  x,w,r=self.setup(w=Conn('off',updated=0))
  with self.assertRaises(PersistenceStopped):x.activate_verified(OP,DIGEST,file_is_verified=True)
  self.assertEqual((w.commits,w.rollbacks),(0,1))
 def test_wrong_writer_denied(self):
  x,w,r=self.setup(w=Conn('on'))
  with self.assertRaises(PersistenceStopped):x.activate_verified(OP,DIGEST,file_is_verified=True)
  self.assertEqual(w.commits,0)
 def test_wrong_role_denied(self):
  x,w,r=self.setup(w=Conn('off',user='dwh_app'))
  with self.assertRaises(PersistenceStopped):x.activate_verified(OP,DIGEST,file_is_verified=True)
  self.assertEqual(w.commits,0)
 def test_reader_mismatch_after_commit(self):
  x,w,r=self.setup(r=Conn('on',count=0))
  with self.assertRaises(PersistenceStopped):x.activate_verified(OP,DIGEST,file_is_verified=True)
  self.assertEqual(w.commits,1)
 def test_db_failure_sanitized(self):
  x,w,r=self.setup(w=Conn('off',fail=True))
  with self.assertRaises(PersistenceStopped) as e:x.activate_verified(OP,DIGEST,file_is_verified=True)
  self.assertNotIn('secret-value',str(e.exception));self.assertEqual(w.commits,0)
 def test_readback_failure_after_outcome(self):
  x,w,r=self.setup(r=Conn('on',count=0))
  with self.assertRaises(PersistenceStopped):x.successful_outcome(OP,DIGEST)
  self.assertEqual(w.commits,1)
 def test_args_are_parameterized(self):
  x,w,r=self.setup();x.activate_verified(OP,DIGEST,file_is_verified=True)
  update=next((s,a) for s,a in w.statements if s.startswith('UPDATE'))
  self.assertNotIn(DIGEST,update[0]);self.assertEqual(update[1][0],DIGEST)
if __name__=='__main__':unittest.main()
