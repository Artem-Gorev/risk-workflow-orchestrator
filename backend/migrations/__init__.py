from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection

CONFIG_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"


def upgrade_database(connection: Connection | None = None) -> None:
    config = Config(CONFIG_PATH)
    if connection is not None:
        config.attributes["connection"] = connection
    command.upgrade(config, "head")
