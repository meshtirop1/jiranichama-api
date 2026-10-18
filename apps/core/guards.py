"""SQL for append-only tables, used by each app's migration.

``jirani_forbid_change()`` is created once (core 0002). ``append_only(table)`` returns the
forward and reverse SQL that attaches it to a table. TRUNCATE is not trapped, so the test
runner can still flush; production revokes TRUNCATE from the application role instead
(see docs/operations/database-roles.md).
"""

FORBID_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION jirani_forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'append-only table %: % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'P0001';
END;
$$;
"""

DROP_FUNCTION_SQL = "DROP FUNCTION IF EXISTS jirani_forbid_change();"


def append_only(table: str) -> tuple[str, str]:
    trigger = f"{table}_append_only"
    forward = (
        f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION jirani_forbid_change();"
    )
    reverse = f"DROP TRIGGER IF EXISTS {trigger} ON {table};"
    return forward, reverse
