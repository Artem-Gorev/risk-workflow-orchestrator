from alembic import op

revision = "007"
down_revision = "006"


def upgrade():
    op.execute("""UPDATE jobs j SET attempt=attempt+1
        WHERE EXISTS (SELECT 1 FROM job_events e WHERE e.job_id=j.id
                        AND e.type='JOB_RETRY_SCHEDULED' AND e.payload->>'attempt'='0')
           OR (attempt=0 AND (execution IS NOT NULL OR started IS NOT NULL
                              OR status IN ('STARTING','RUNNING')))""")
    op.execute("""WITH legacy AS (
        SELECT job_id,min(id) AS first_retry FROM job_events
        WHERE type='JOB_RETRY_SCHEDULED' AND payload->>'attempt'='0'
        GROUP BY job_id
    )
    UPDATE job_events e
       SET payload=jsonb_set(e.payload,'{attempt}',to_jsonb((e.payload->>'attempt')::int+1))
      FROM legacy
     WHERE e.job_id=legacy.job_id AND e.id>=legacy.first_retry
       AND e.type IN ('JOB_RESERVED','JOB_RETRY_SCHEDULED') AND e.payload ? 'attempt'""")


def downgrade():
    # Corrected counts remain valid under the previous schema.
    pass
