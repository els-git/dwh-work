import unittest
from dwh_api.secret_admin_executor import (
    AdminConnectionStopped, AdminTarget, InteractiveAdminConnections
)


class Cursor:
    def __init__(self, connection): self.conn = connection
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql):
        self.conn.statements.append(sql)
        if self.conn.fail: raise RuntimeError('sensitive error')
        self.sql = sql
    def fetchone(self):
        if self.sql == 'SHOW transaction_read_only': return {'transaction_read_only': self.conn.mode}
        return {'current_database': self.conn.database, 'current_user': self.conn.user}


class Conn:
    def __init__(self, mode, database='dwh', user='dwh_admin', fail=False):
        self.mode, self.database, self.user, self.fail = mode, database, user, fail
        self.statements = []
        self.closed = False
        self.rollbacks = 0
    def cursor(self): return Cursor(self)
    def rollback(self): self.rollbacks += 1
    def close(self): self.closed = True


class Tests(unittest.TestCase):
    def provider(self, mutate=None):
        conns = []
        args = []
        def connector(**kwargs):
            args.append(kwargs)
            c = Conn('on' if 'read_only=on' in kwargs['options'] else 'off')
            if mutate: mutate(c)
            conns.append(c)
            return c
        return InteractiveAdminConnections(AdminTarget(), 'test-password', connector), conns, args

    def test_preflight_both_sessions(self):
        p, conns, _ = self.provider()
        p.verify_only()
        self.assertEqual([c.mode for c in conns], ['on', 'off'])
        self.assertTrue(all(c.closed for c in conns))
        self.assertTrue(all(c.rollbacks == 1 for c in conns))
    def test_no_secret_store_sql(self):
        p, conns, _ = self.provider(); p.verify_only()
        self.assertTrue(all(len(c.statements) == 2 for c in conns))
        self.assertFalse(any('secret_store' in s for c in conns for s in c.statements))
    def test_no_password_logging(self):
        p, _, args = self.provider(); p.verify_only()
        self.assertEqual(args[0]['password'], 'test-password')
        self.assertNotIn('test-password', repr(p))
    def test_wrong_database_rejected(self):
        with self.assertRaises(AdminConnectionStopped):
            InteractiveAdminConnections(AdminTarget(dbname='other'), 'x', lambda **kw: None)
    def test_wrong_role_rejected(self):
        with self.assertRaises(AdminConnectionStopped):
            InteractiveAdminConnections(AdminTarget(user='dwh_app'), 'x', lambda **kw: None)
    def test_wrong_host_rejected(self):
        with self.assertRaises(AdminConnectionStopped):
            InteractiveAdminConnections(AdminTarget(host='localhost'), 'x', lambda **kw: None)
    def test_wrong_port_rejected(self):
        with self.assertRaises(AdminConnectionStopped):
            InteractiveAdminConnections(AdminTarget(port=5432), 'x', lambda **kw: None)
    def test_no_password_rejected(self):
        with self.assertRaises(AdminConnectionStopped):
            InteractiveAdminConnections(AdminTarget(), '', lambda **kw: None)
    def test_nonreadonly_reader_rejected_and_closed(self):
        p, conns, _ = self.provider(lambda c: setattr(c, 'mode', 'off'))
        with self.assertRaises(AdminConnectionStopped): p.reader()
        self.assertTrue(conns[0].closed)
    def test_readonly_writer_rejected(self):
        p, conns, _ = self.provider(lambda c: setattr(c, 'mode', 'on'))
        with self.assertRaises(AdminConnectionStopped): p.writer()
        self.assertTrue(conns[0].closed)
    def test_wrong_identity_rejected(self):
        p, conns, _ = self.provider(lambda c: setattr(c, 'user', 'dwh_app'))
        with self.assertRaises(AdminConnectionStopped): p.reader()
        self.assertTrue(conns[0].closed)
    def test_connector_error_masked(self):
        p = InteractiveAdminConnections(AdminTarget(), 'x', lambda **kw: (_ for _ in ()).throw(ValueError('secret')))
        with self.assertRaises(AdminConnectionStopped) as cm: p.reader()
        self.assertNotIn('secret', str(cm.exception))
    def test_cursor_error_masked(self):
        p, conns, _ = self.provider(lambda c: setattr(c, 'fail', True))
        with self.assertRaises(AdminConnectionStopped) as cm: p.reader()
        self.assertNotIn('sensitive', str(cm.exception))
        self.assertTrue(conns[0].closed)

if __name__ == '__main__': unittest.main()
