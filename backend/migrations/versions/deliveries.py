from alembic import op

revision = "004"
down_revision = "003"


def upgrade():
    op.execute("ALTER TABLE processes ADD join_active boolean NOT NULL DEFAULT false")
    op.execute("""CREATE TABLE deliveries (key text PRIMARY KEY, job_id uuid NOT NULL REFERENCES jobs,
               priority integer, restarted_from text)""")
    op.execute("""INSERT INTO deliveries SELECT delivery,id,requested_priority,trigger->>'restartedFrom'
               FROM jobs WHERE delivery IS NOT NULL""")
    op.execute("""CREATE TABLE configuration_events(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
               occurred timestamptz NOT NULL DEFAULT clock_timestamp(), actor text NOT NULL,
               operation text NOT NULL, object_id text NOT NULL, details jsonb NOT NULL)""")


def downgrade():
    op.execute("DROP TABLE configuration_events")
    op.execute("DROP TABLE deliveries")
    op.execute("ALTER TABLE processes DROP join_active")
