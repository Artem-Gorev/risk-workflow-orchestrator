from alembic import op

revision = "006"
down_revision = "005"


def upgrade():
    op.execute("""CREATE TABLE risk_batches (
        id uuid PRIMARY KEY, day date NOT NULL, factor_revision bigint NOT NULL,
        expected jsonb NOT NULL, actor text NOT NULL, status text NOT NULL DEFAULT 'RUNNING',
        error text, created timestamptz NOT NULL DEFAULT clock_timestamp())""")
    op.execute("""ALTER TABLE jobs ADD snapshot jsonb, ADD attempt integer NOT NULL DEFAULT 0,
        ADD retry_at timestamptz, ADD batch_id uuid REFERENCES risk_batches,
        ADD replay_of uuid REFERENCES jobs""")
    op.execute("CREATE UNIQUE INDEX batch_steps ON jobs(batch_id,process) WHERE batch_id IS NOT NULL")
    op.execute("""CREATE TABLE schedule_history(id bigint GENERATED ALWAYS AS IDENTITY UNIQUE, schedule_id
        text, planned timestamptz,
        observed timestamptz NOT NULL, status text NOT NULL, reason text, job_id uuid REFERENCES jobs,
        PRIMARY KEY(schedule_id,planned))""")
    op.execute("""CREATE TABLE factor_history(revision bigint PRIMARY KEY, value jsonb NOT NULL,
        source uuid REFERENCES jobs)""")
    op.execute("""INSERT INTO factor_history SELECT revision,value,source FROM resource_values
        WHERE key='risk_parameters' AND partition='*'""")
    op.execute("""INSERT INTO factor_history SELECT (o->>'revision')::bigint,o->'value',j.id
        FROM jobs j CROSS JOIN LATERAL jsonb_array_elements(j.result->'outputs') o
        WHERE o->>'resourceKey'='risk_parameters' ON CONFLICT DO NOTHING""")
    op.execute("""INSERT INTO factor_history VALUES (1,'{"creditFactor":"0.10","marketFactor":"0.20"}',NULL)
        ON CONFLICT DO NOTHING""")
    op.execute("""CREATE TABLE benchmark_results(id uuid PRIMARY KEY, created timestamptz NOT NULL
        DEFAULT clock_timestamp(), report jsonb NOT NULL)""")


def downgrade():
    op.execute("DROP TABLE benchmark_results, schedule_history, factor_history")
    op.execute("ALTER TABLE jobs DROP snapshot, DROP attempt, DROP retry_at, DROP batch_id, DROP replay_of")
    op.execute("DROP TABLE risk_batches")
