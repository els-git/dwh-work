import unittest
from dwh_api.secret_create_coordinator import CreateCoordinator, Request, Receipt, CoordinationStopped

R = Request('ref-synthetic', 1, 'op-synthetic')

class Adapter:
    def __init__(self, fail=None, wrong=None):
        self.log=[]
        self.fail=fail
        self.wrong=wrong
    def give(self, phase):
        self.log.append(phase)
        if self.fail == phase: raise RuntimeError('sensitive underlying detail')
        return Receipt(phase, R.operation_id if self.wrong != phase else 'other',
                       R.secret_ref,R.source_system_id,True,'synthetic-id')
    def commit_attempt(self, r): return self.give('ATTEMPT')
    def commit_pending(self, r): return self.give('PENDING')
    def create_durable(self, r):
        self.log.append('FILE')
        if self.fail == 'FILE': raise RuntimeError('secret text')
        return 'a'*64 if self.wrong != 'FILE' else 'invalid'
    def commit_outcome(self, r, digest): return self.give('OUTCOME')
    def commit_failure(self, r): return self.give('OUTCOME_FAILED')
    def activate(self, r, digest): return self.give('ACTIVE')

class Tests(unittest.TestCase):
    def test_success_order(self):
        a=Adapter(); out=CreateCoordinator(a,a,a).create(R)
        self.assertEqual(out.phase,'ACTIVE')
        self.assertEqual(a.log,['ATTEMPT','PENDING','FILE','OUTCOME','ACTIVE'])
    def test_denied_attempt_no_later_effects(self):
        a=Adapter(fail='ATTEMPT')
        with self.assertRaises(CoordinationStopped): CreateCoordinator(a,a,a).create(R)
        self.assertEqual(a.log,['ATTEMPT'])
    def test_failure_each_step_fail_closed(self):
        for phase in ('PENDING','FILE','OUTCOME','ACTIVE'):
            with self.subTest(phase=phase):
                a=Adapter(fail=phase)
                with self.assertRaises(CoordinationStopped): CreateCoordinator(a,a,a).create(R)
                self.assertNotIn('ACTIVE',a.log) if phase != 'ACTIVE' else None
    def test_mismatched_receipts(self):
        for phase in ('ATTEMPT','PENDING','FILE','OUTCOME','ACTIVE'):
            with self.subTest(phase=phase):
                a=Adapter(wrong=phase)
                with self.assertRaises(CoordinationStopped): CreateCoordinator(a,a,a).create(R)
    def test_invalid_requests(self):
        for source in (0,-1,True):
            with self.assertRaises(CoordinationStopped):
                a=Adapter();CreateCoordinator(a,a,a).create(Request('ref',source,'op'))
            self.assertEqual(a.log,[])
    def test_no_sensitive_exception(self):
        a=Adapter(fail='FILE')
        with self.assertRaises(CoordinationStopped) as cm: CreateCoordinator(a,a,a).create(R)
        self.assertNotIn('secret text',str(cm.exception))
    def test_failed_outcome_is_not_success(self):
        a=Adapter(fail='FILE')
        with self.assertRaises(CoordinationStopped):CreateCoordinator(a,a,a).create(R)
        self.assertEqual(a.log,['ATTEMPT','PENDING','FILE','OUTCOME_FAILED'])

if __name__=='__main__': unittest.main(verbosity=2)
