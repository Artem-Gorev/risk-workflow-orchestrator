import re
from hashlib import sha256

from sqlalchemy.engine import make_url

from backend.migrations import upgrade_database

from .storage import query


def demo_schema(storage):
    with storage.engine.connect() as conn:
        parent = query(conn, "SELECT current_schema()").scalar_one()
    return "risk_demo_" + sha256(parent.encode()).hexdigest()[:16]


def provision(storage, name):
    if not re.fullmatch(r"risk_(?:demo|bench)_[a-f0-9]+", name):
        raise ValueError("Некорректное имя схемы")
    with storage.engine.begin() as conn:
        query(conn, f'CREATE SCHEMA IF NOT EXISTS "{name}"')
        query(conn, f'SET LOCAL search_path TO "{name}"')
        upgrade_database(conn)
    return make_url(storage.engine.url).update_query_dict({"options": f"-csearch_path={name}"})


def job_storage(runtime, job_id):
    if job_id:
        with runtime.storage.engine.connect() as conn:
            if not query(conn, "SELECT 1 FROM jobs WHERE id=:id", id=job_id).first():
                return runtime.demo.storage
    return runtime.storage
