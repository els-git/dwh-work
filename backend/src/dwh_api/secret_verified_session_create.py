"""Opt-in administrative SecretStore CREATE authenticated by a fresh DWH session.

No implicit database writes. The injected create operation must be operator-owned.
This does not disable legacy/disconnected administrative call sites.
"""
from __future__ import annotations
import hashlib
from dwh_api.secret_create_coordinator import Request
from dwh_api.secret_source_scope import authorize_source_secret_create

class SessionCreateStopped(Exception):
    pass

def verified_session_create(*, raw_session_token: str, request: Request,
                            readonly_connection, execute_authorized_create,
                            session_loader=None):
    """Revalidate a session directly from DWH before any privileged CREATE.

    session_loader injection is for isolated tests; production uses auth DB reader.
    Caller MUST get token through non-logging interactive channel and control the
    request/source mapping and execute_authorized_create closure. No secrets logged.
    """
    try:
        if (not isinstance(raw_session_token, str) or not raw_session_token
                or len(raw_session_token) > 512 or not isinstance(request, Request)
                or not callable(readonly_connection)
                or not callable(execute_authorized_create)):
            raise ValueError()
        if session_loader is None:
            from dwh_api.auth import _load_auth_state_by_hash
            session_loader = _load_auth_state_by_hash
        if not callable(session_loader):
            raise ValueError()
        token_hash = hashlib.sha256(raw_session_token.encode('utf-8')).hexdigest()
        state = session_loader(token_hash)
        if state is None or not authorize_source_secret_create(
                state, request.source_system_id, readonly_connection=readonly_connection):
            raise ValueError()
    except Exception:
        raise SessionCreateStopped('DWH session or source authorization rejected') from None
    # Deliberately do not swallow errors here: a partial COMMIT requires reconciliation.
    return execute_authorized_create(state)
