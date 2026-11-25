from alembic import op

revision = "008"
down_revision = "007"

RESERVATIONS = """CREATE OR REPLACE VIEW reservations AS
    SELECT j.id AS job_id,j.execution,a->>'resourceKey' AS resource_key,a->'scope' AS scope,a->>'mode' AS mode
    FROM jobs j CROSS JOIN LATERAL jsonb_array_elements(j.accesses) a
    WHERE j.status IN ('STARTING','RUNNING')"""


def upgrade():
    op.execute("ALTER TABLE risk_batches ADD delivery_key text, ADD UNIQUE (actor,delivery_key)")
    op.execute("""UPDATE jobs j SET accesses=(
        SELECT jsonb_agg(CASE
            WHEN j.process='risk-summary' OR a->>'mode'='WRITE' OR a->>'resourceKey'='risk_parameters'
            THEN a || '{"isolated": true}'::jsonb ELSE a END ORDER BY position)
        FROM jsonb_array_elements(j.accesses) WITH ORDINALITY AS entries(a,position)
    ) WHERE j.batch_id IS NOT NULL AND jsonb_array_length(j.accesses)>0""")
    op.execute(RESERVATIONS + " AND a->>'isolated' IS DISTINCT FROM 'true'")
    op.execute("""UPDATE risk_batches b SET status='FAILED',
        error='Расхождение с контрольными значениями · ' || mismatches.processes
        FROM (
            SELECT j.batch_id,string_agg(j.process,', ' ORDER BY j.seq) AS processes
            FROM jobs j JOIN risk_batches r ON r.id=j.batch_id
            WHERE r.status='SUCCEEDED'
              AND (r.expected->j.process) IS DISTINCT FROM (j.result->'outputs'->0->'value')
            GROUP BY j.batch_id
        ) mismatches WHERE b.id=mismatches.batch_id""")


def downgrade():
    op.execute(RESERVATIONS)
    op.execute("""UPDATE jobs j SET accesses=(
        SELECT jsonb_agg(a-'isolated' ORDER BY position)
        FROM jsonb_array_elements(j.accesses) WITH ORDINALITY AS entries(a,position)
    ) WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(j.accesses) a WHERE a ? 'isolated')""")
    op.execute("ALTER TABLE risk_batches DROP COLUMN delivery_key")
