from alembic import op

revision = "003"
down_revision = "002"


def upgrade():
    op.execute("""ALTER TABLE processes ADD handler text, ADD description text NOT NULL DEFAULT '',
        ADD priority int NOT NULL DEFAULT 5 CHECK(priority BETWEEN 1 AND 9),
        ADD duration double precision NOT NULL DEFAULT 0 CHECK(duration>=0),
        ADD active boolean NOT NULL DEFAULT true, ADD bindings jsonb NOT NULL DEFAULT '{}'""")
    op.execute("ALTER TABLE resources ADD kind text")
    op.execute("""ALTER TABLE jobs ADD handler text, ADD bindings jsonb NOT NULL DEFAULT '{}',
        ADD priority int NOT NULL DEFAULT 5, ADD requested_priority int,
        ADD duration double precision NOT NULL DEFAULT 0, ADD lease_until timestamptz""")
    op.execute("""ALTER TABLE schedules ADD local_time time NOT NULL DEFAULT '02:00',
        ADD weekday int CHECK(weekday BETWEEN 0 AND 6), ADD enabled boolean NOT NULL DEFAULT true""")
    op.execute("CREATE INDEX jobs_active_lease ON jobs(lease_until) WHERE status IN ('STARTING','RUNNING')")


def downgrade():
    op.execute("DROP INDEX jobs_active_lease")
    op.execute("ALTER TABLE schedules DROP local_time,DROP weekday,DROP enabled")
    op.execute(
        "ALTER TABLE jobs DROP handler,DROP bindings,DROP priority,DROP requested_priority,"
        "DROP duration,DROP lease_until"
    )
    op.execute("ALTER TABLE resources DROP kind")
    op.execute(
        "ALTER TABLE processes DROP handler,DROP description,DROP priority,DROP duration,"
        "DROP active,DROP bindings"
    )
