-- Rollback of 027: removes only the marker. The revocation itself is journaled and irreversible
-- (secret_store keeps every event; a REVOKED binding cannot become ACTIVE again); data only from the copy pre-027.
DROP FUNCTION secret_store.legacy_bindings_revoked();
SELECT 'ROLLBACK_027_DONE' AS result;
