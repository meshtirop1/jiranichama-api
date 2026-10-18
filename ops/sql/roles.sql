-- Production database roles. Run once as the database owner after the first migrate,
-- and again after any migration that adds a table. See docs/operations/database-roles.md.
--
--   jiranichama_owner : owns every table, runs migrations. Never used by the app.
--   jiranichama_app   : what Django and Celery connect as.

-- Ordinary tables: the app may read and write.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO jiranichama_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO jiranichama_app;

-- Append-only tables: INSERT and SELECT only. The triggers refuse UPDATE and DELETE as
-- well; the missing grants mean even a disabled trigger cannot be exploited by the app.
REVOKE UPDATE, DELETE, TRUNCATE ON ledger_entry, ledger_entry_signature, staff_audit_log
    FROM jiranichama_app;

-- Chain heads are updated on every append, but never deleted.
REVOKE DELETE, TRUNCATE ON ledger_chain_head, staff_audit_head FROM jiranichama_app;

-- Nobody but the owner may change triggers.
REVOKE TRIGGER ON ALL TABLES IN SCHEMA public FROM jiranichama_app;
