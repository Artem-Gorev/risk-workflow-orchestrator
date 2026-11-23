from alembic import op

revision = "002"
down_revision = "001"


def upgrade():
    op.execute("""CREATE TABLE resources (resource_key text PRIMARY KEY,
        partitioning text NOT NULL CHECK(partitioning IN ('GLOBAL','BY_DATE')))""")
    op.execute("""CREATE TABLE processes (process_id text, version integer, name text NOT NULL,
        access_templates jsonb NOT NULL, PRIMARY KEY(process_id,version))""")
    op.execute("""CREATE VIEW reservations AS SELECT j.id AS job_id, j.execution, a->>'resourceKey' AS
        resource_key,
        a->'scope' AS scope,a->>'mode' AS mode FROM jobs j CROSS JOIN LATERAL
        jsonb_array_elements(j.accesses) a
        WHERE j.status IN ('STARTING','RUNNING')""")
    op.execute("CREATE INDEX jobs_process_day_seq ON jobs(process,day,seq)")
    op.execute("CREATE INDEX schedules_due ON schedules(due) WHERE due IS NOT NULL")


def downgrade():
    op.execute("DROP INDEX schedules_due")
    op.execute("DROP INDEX jobs_process_day_seq")
    op.execute("DROP VIEW reservations")
    op.execute("DROP TABLE processes")
    op.execute("DROP TABLE resources")
