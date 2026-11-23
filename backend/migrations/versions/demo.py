from alembic import op

revision = "005"
down_revision = "004"


def upgrade():
    op.execute("""CREATE TABLE demo_runs (
        id uuid PRIMARY KEY, first_job uuid NOT NULL REFERENCES jobs,
        scheduled_job uuid NOT NULL, independent_job uuid NOT NULL,
        status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','DELIVERED','FAILED')),
        error text
    )""")
    op.execute("ALTER TABLE schedules ADD demo_id uuid UNIQUE REFERENCES demo_runs")


def downgrade():
    op.execute("ALTER TABLE schedules DROP demo_id")
    op.execute("DROP TABLE demo_runs")
