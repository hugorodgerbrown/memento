"""
Principle 1 ("raw is sacred") at the database layer. No code path, including
QuerySet.update() and raw SQL from the app, can rewrite what you said.
"""

from django.db import migrations

FORWARD = """
CREATE FUNCTION memento_entry_is_sacred() RETURNS trigger AS $$
BEGIN
    IF NEW.raw_text IS DISTINCT FROM OLD.raw_text
       OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
       OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at THEN
        RAISE EXCEPTION 'raw_text, owner and recorded_at are immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER entry_is_sacred
    BEFORE UPDATE ON memories_entry
    FOR EACH ROW EXECUTE FUNCTION memento_entry_is_sacred();

CREATE FUNCTION memento_capture_is_sacred() RETURNS trigger AS $$
BEGIN
    IF NEW.text IS DISTINCT FROM OLD.text
       OR NEW.segments IS DISTINCT FROM OLD.segments
       OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
       OR NEW.external_id IS DISTINCT FROM OLD.external_id
       OR NEW.captured_at IS DISTINCT FROM OLD.captured_at THEN
        RAISE EXCEPTION 'a capture transcript is immutable; append a revision instead'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- Revisions are append-only: nothing already recorded may be removed or altered.
    IF jsonb_array_length(NEW.revisions) < jsonb_array_length(OLD.revisions)
       OR NOT (NEW.revisions @> OLD.revisions) THEN
        RAISE EXCEPTION 'revisions are append-only'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER capture_is_sacred
    BEFORE UPDATE ON memories_capture
    FOR EACH ROW EXECUTE FUNCTION memento_capture_is_sacred();
"""

REVERSE = """
DROP TRIGGER IF EXISTS capture_is_sacred ON memories_capture;
DROP FUNCTION IF EXISTS memento_capture_is_sacred();
DROP TRIGGER IF EXISTS entry_is_sacred ON memories_entry;
DROP FUNCTION IF EXISTS memento_entry_is_sacred();
"""


class Migration(migrations.Migration):
    dependencies = [("memories", "0001_initial")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
