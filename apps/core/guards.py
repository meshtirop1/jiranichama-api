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


FORBID_EXCEPT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION jirani_forbid_change_except() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    allowed text[] := TG_ARGV;
    n jsonb;
    o jsonb;
    k text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'append-only table %: DELETE is not allowed', TG_TABLE_NAME
            USING ERRCODE = 'P0001';
    END IF;
    n := to_jsonb(NEW);
    o := to_jsonb(OLD);
    FOREACH k IN ARRAY allowed LOOP
        n := n - k;
        o := o - k;
    END LOOP;
    IF n IS DISTINCT FROM o THEN
        RAISE EXCEPTION 'append-only table %: only % may change', TG_TABLE_NAME, allowed
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$;
"""

DROP_EXCEPT_FUNCTION_SQL = "DROP FUNCTION IF EXISTS jirani_forbid_change_except();"


def append_only_except(table: str, columns: list[str]) -> tuple[str, str]:
    """Rows are written once, but these status columns may still move (for example a
    statement's ``state``). Everything else, and DELETE, is refused."""
    trigger = f"{table}_append_only"
    args = ", ".join(f"'{c}'" for c in columns)
    forward = (
        f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION jirani_forbid_change_except({args});"
    )
    reverse = f"DROP TRIGGER IF EXISTS {trigger} ON {table};"
    return forward, reverse


def append_only(table: str) -> tuple[str, str]:
    trigger = f"{table}_append_only"
    forward = (
        f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION jirani_forbid_change();"
    )
    reverse = f"DROP TRIGGER IF EXISTS {trigger} ON {table};"
    return forward, reverse
