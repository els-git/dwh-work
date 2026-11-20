-- Rollback of 028: removes the four functions. Renamed or deleted objects stay as they are (their audit events
-- remain); a deleted object can only come back from the copy pre-028.
DROP FUNCTION app.integration_source_object_delete(text, bigint);
DROP FUNCTION app.integration_source_object_rename(text, bigint, text, text);
DROP FUNCTION app.integration_source_object_usage(text, bigint);
DROP FUNCTION meta.source_object_usage(bigint);
SELECT 'ROLLBACK_028_DONE' AS result;
