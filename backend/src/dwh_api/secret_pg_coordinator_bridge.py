"""Fail-closed bridge between existing CREATE coordinator and PG durable adapter.

This intentionally supports only already implemented ATTEMPT/PENDING/FAILED paths.
It does not synthesize successful OUTCOME/ACTIVE or prove filesystem durability.
"""
from dwh_api.secret_create_coordinator import Receipt, CoordinationStopped
from dwh_api.secret_pg_durable_adapter import DbOperation


class PgCoordinatorBridge:
    """One bridge supplies both DurableAudit and BindingRegistry partial protocols."""

    def __init__(self, adapter):
        if not all(callable(getattr(adapter, n, None)) for n in
                   ('permitted_attempt', 'pending', 'failed_outcome')):
            raise CoordinationStopped('Trusted persistence adapter required')
        self._adapter = adapter

    @staticmethod
    def _operation(request):
        try:
            return DbOperation(operation_id=request.operation_id,
                               secret_ref=request.secret_ref,
                               source_system_id=request.source_system_id)
        except Exception:
            raise CoordinationStopped('Invalid trusted request') from None

    @staticmethod
    def _receipt(request, phase):
        return Receipt(phase=phase, operation_id=request.operation_id,
                       secret_ref=request.secret_ref,
                       source_system_id=request.source_system_id,
                       committed=True, evidence_id='db-readback:' + phase)

    def commit_attempt(self, request):
        op = self._operation(request)
        try:
            self._adapter.permitted_attempt(op)
        except Exception:
            raise CoordinationStopped('ATTEMPT not verified') from None
        return self._receipt(request, 'ATTEMPT')

    def commit_pending(self, request):
        op = self._operation(request)
        try:
            self._adapter.pending(op)
        except Exception:
            raise CoordinationStopped('PENDING not verified') from None
        return self._receipt(request, 'PENDING')

    def commit_failure(self, request):
        op = self._operation(request)
        try:
            self._adapter.failed_outcome(op)
        except Exception:
            raise CoordinationStopped('FAILED OUTCOME not verified') from None
        return self._receipt(request, 'OUTCOME_FAILED')

    def commit_outcome(self, request, file_digest):
        raise CoordinationStopped('Successful OUTCOME not implemented')

    def activate(self, request, file_digest):
        raise CoordinationStopped('ACTIVE publication not implemented')
