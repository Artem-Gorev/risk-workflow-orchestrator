from alembic import op

revision = "001"
down_revision = None


def upgrade():
    op.execute("CREATE TABLE orchestrator_state (id integer PRIMARY KEY CHECK(id=1))")
    op.execute("INSERT INTO orchestrator_state VALUES (1)")
    op.execute("""CREATE TABLE jobs (
        id uuid PRIMARY KEY, seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE, process text NOT NULL,
        day date NOT NULL, version integer NOT NULL DEFAULT 1, trigger jsonb NOT NULL, delivery text UNIQUE,
        accesses jsonb NOT NULL, status text NOT NULL CHECK (status IN
        ('QUEUED','WAITING','STARTING','RUNNING','FAILED','SUCCEEDED','CANCELLED')),
        execution uuid UNIQUE, reason jsonb, result jsonb, error jsonb,
        queued timestamptz NOT NULL DEFAULT clock_timestamp(), started timestamptz, finished timestamptz)""")
    op.execute("CREATE INDEX jobs_queue ON jobs(status,seq)")
    op.execute("""CREATE TABLE job_events (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        job_id uuid NOT NULL REFERENCES jobs, type text NOT NULL, status text NOT NULL,
        occurred timestamptz NOT NULL, payload jsonb NOT NULL)""")
    op.execute("CREATE INDEX events_by_job ON job_events(job_id,id)")
    op.execute("""CREATE TABLE resource_values (key text, partition text, value jsonb NOT NULL,
        revision bigint NOT NULL DEFAULT 1, source uuid REFERENCES jobs, PRIMARY KEY(key,partition))""")
    op.execute("""CREATE TABLE schedules (id text PRIMARY KEY, process text NOT NULL, due timestamptz,
        day date, daily boolean NOT NULL DEFAULT false)""")


def downgrade():
    for table in ("schedules", "resource_values", "job_events", "jobs", "orchestrator_state"):
        op.drop_table(table)
