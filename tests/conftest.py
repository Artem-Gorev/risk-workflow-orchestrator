import os
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session")
def postgres_url():
    if url := os.getenv("TEST_DATABASE_URL"):
        yield url
        return
    try:
        pgserver = import_module("pgserver")
    except ModuleNotFoundError as error:
        if error.name != "pgserver":
            raise
        message = "Set TEST_DATABASE_URL to a PostgreSQL test database or install pgserver."
        raise pytest.UsageError(message) from None

    with TemporaryDirectory(prefix="riskpg-", dir="/tmp") as directory:
        server = pgserver.get_server(Path(directory))
        try:
            yield server.get_uri().replace("postgresql://", "postgresql+psycopg://")
        finally:
            server.cleanup()


@pytest.fixture
def db(postgres_url, monkeypatch, tmp_path):
    schema = "test_" + uuid4().hex
    admin = create_engine(postgres_url)
    with admin.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    url = make_url(postgres_url)
    options = url.query.get("options", os.getenv("PGOPTIONS", ""))
    url = url.update_query_dict({"options": f"{options} -csearch_path={schema}".strip()})
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    monkeypatch.setenv("BACKEND_LOCK_PATH", str(tmp_path / "lock"))
    monkeypatch.setenv("DISPATCH_POLL_SECONDS", "0.02")
    monkeypatch.setenv("SCHEDULER_POLL_SECONDS", "1")
    monkeypatch.setenv("DAILY_ENABLED", "0")
    monkeypatch.setenv("WORKER_DELAY_SECONDS", "0")
    monkeypatch.setenv("WORKER_SLOTS", "2")
    monkeypatch.setenv("AUTH_MODE", "local")
    for name in ("PUBLIC_ORIGIN", "DEMO_INITIATOR", "SYNTHETIC_START_DATE", "SYNTHETIC_END_DATE"):
        monkeypatch.delenv(name, raising=False)
    yield
    with admin.begin() as conn:
        demo = "risk_demo_" + sha256(schema.encode()).hexdigest()[:16]
        conn.execute(text(f"DROP SCHEMA IF EXISTS {demo} CASCADE"))
        conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()
