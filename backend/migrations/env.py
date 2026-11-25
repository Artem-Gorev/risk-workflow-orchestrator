import os

from alembic import context
from sqlalchemy import create_engine


def migrate(connection):
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is None:
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            migrate(connection)
    finally:
        engine.dispose()
else:
    migrate(connection)
