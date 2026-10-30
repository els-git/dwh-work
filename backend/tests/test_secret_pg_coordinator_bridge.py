import unittest
from dwh_api.secret_create_coordinator import CreateCoordinator, Request, CoordinationStopped
from dwh_api.secret_pg_coordinator_bridge import PgCoordinatorBridge

REQ = Request('60890348-eacf-4128-9f91-31092a5d7a96', 1,
              'faf89680-0a99-45f2-a7e7-63f04de3bbb2')

class FakeDb:
    def __init__(self, fail=None): self.calls=[]; self.fail=fail
    def _step(self, name, op):
        self.calls.append(name)
        if self.fail == name: raise RuntimeError('RAW SECRET')
    def permitted_attempt(self, op): self._step('ATTEMPT', op)
    def pending(self, op): self._step('PENDING', op)
    def failed_outcome(self, op): self._step('FAILED', op)

class FileFailure:
    def create_durable(self, request): raise RuntimeError('RAW SECRET')

class FileFakeSuccess:
    def create_durable(self, request): return 'a'*64

class Tests(unittest.TestCase):
    def test_failure_path_orders_and_does_not_activate(self):
        db=FakeDb(); b=PgCoordinatorBridge(db)
        with self.assertRaisesRegex(CoordinationStopped, 'manual reconciliation'):
            CreateCoordinator(b,b,FileFailure()).create(REQ)
        self.assertEqual(db.calls, ['ATTEMPT','PENDING','FAILED'])
    def test_successful_file_still_refuses_to_activate(self):
        db=FakeDb();b=PgCoordinatorBridge(db)
        with self.assertRaises(CoordinationStopped):
            CreateCoordinator(b,b,FileFakeSuccess()).create(REQ)
        self.assertEqual(db.calls, ['ATTEMPT','PENDING','FAILED'])
    def test_failed_attempt_does_not_write_failure(self):
        db=FakeDb('ATTEMPT');b=PgCoordinatorBridge(db)
        with self.assertRaises(CoordinationStopped):
            CreateCoordinator(b,b,FileFailure()).create(REQ)
        self.assertEqual(db.calls,['ATTEMPT'])
    def test_failed_pending_attempts_failed_outcome(self):
        db=FakeDb('PENDING');b=PgCoordinatorBridge(db)
        with self.assertRaises(CoordinationStopped):
            CreateCoordinator(b,b,FileFailure()).create(REQ)
        self.assertEqual(db.calls,['ATTEMPT','PENDING','FAILED'])
    def test_failed_outcome_never_reports_success(self):
        db=FakeDb('FAILED');b=PgCoordinatorBridge(db)
        with self.assertRaises(CoordinationStopped):
            CreateCoordinator(b,b,FileFailure()).create(REQ)
        self.assertEqual(db.calls,['ATTEMPT','PENDING','FAILED'])
    def test_safe_errors(self):
        db=FakeDb('ATTEMPT');b=PgCoordinatorBridge(db)
        try: b.commit_attempt(REQ)
        except CoordinationStopped as exc: self.assertNotIn('RAW SECRET',str(exc))
    def test_receipt_after_adapter_only(self):
        db=FakeDb();b=PgCoordinatorBridge(db)
        receipt=b.commit_attempt(REQ)
        self.assertTrue(receipt.committed);self.assertEqual(db.calls,['ATTEMPT'])
    def test_missing_adapter_refused(self):
        with self.assertRaises(CoordinationStopped): PgCoordinatorBridge(object())
    def test_no_activation_method_on_db(self):
        self.assertFalse(hasattr(FakeDb(),'activate'))
    def test_malformed_request_denied(self):
        b=PgCoordinatorBridge(FakeDb())
        with self.assertRaises(CoordinationStopped): b.commit_attempt(object())
