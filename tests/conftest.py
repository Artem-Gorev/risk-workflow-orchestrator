import os
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text


@pytest.fixture(scope="session")
def postgres_url():
    if os.getenv("TEST_DATABASE_URL"):
        yield os.environ["TEST_DATABASE_URL"]
        return
    from pgserver import get_server

    with TemporaryDirectory(prefix="riskpg-", dir="/tmp") as directory:
        server = get_server(Path(directory))
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
    monkeypatch.setenv("PGOPTIONS", f"-c search_path={schema}")
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    monkeypatch.setenv("BACKEND_LOCK_PATH", str(tmp_path / "lock"))
    monkeypatch.setenv("DISPATCH_POLL_SECONDS", "0.02")
    monkeypatch.setenv("DAILY_ENABLED", "0")
    monkeypatch.setenv("WORKER_DELAY_SECONDS", "0")
    monkeypatch.setenv("AUTH_MODE", "local")
    yield
    with admin.begin() as conn:
        demo = "risk_demo_" + sha256(schema.encode()).hexdigest()[:16]
        conn.execute(text(f"DROP SCHEMA IF EXISTS {demo} CASCADE"))
        conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()
