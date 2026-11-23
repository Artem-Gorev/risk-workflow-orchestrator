from backend.orchestrator.storage import DomainError, encode, query
from backend.workers.catalog import PROCESSES


def latest(conn, code):
    row = query(
        conn,
        "SELECT * FROM processes WHERE process_id=:code ORDER BY version DESC LIMIT 1",
        code=code,
    ).first()
    if not row:
        raise DomainError("UNKNOWN_PROCESS", "Неизвестный процесс", 422)
    return row


def process_info(row):
    return {
        "processId": row.process_id,
        "processVersion": row.version,
        "name": row.name,
        "description": row.description,
        "handler": row.handler or row.process_id,
        "bindings": row.bindings,
        "joinActive": row.join_active,
        "priority": row.priority,
        "executionSeconds": row.duration,
        "isActive": row.active,
        "accessTemplates": row.access_templates,
    }


def save_process(conn, code, spec, expected):
    version = query(conn, "SELECT max(version) FROM processes WHERE process_id=:code", code=code).scalar()
    if (version or 0) != expected:
        raise DomainError("VERSION_CONFLICT", "Версия процесса изменилась")
    handler = PROCESSES.get(spec.handler)
    if not handler:
        raise DomainError("UNKNOWN_HANDLER", "Неизвестный обработчик", 422)
    templates = handler.templates()
    required = {a["resourceKey"] for a in templates}
    if set(spec.bindings) - required:
        raise DomainError("INVALID_BINDING", "Лишнее имя ресурса", 422)
    for access in templates:
        logical = access["resourceKey"]
        physical = spec.bindings.get(logical, logical)
        kind = query(
            conn,
            "SELECT coalesce(kind,resource_key) FROM resources WHERE resource_key=:key",
            key=physical,
        ).scalar()
        if kind != logical:
            raise DomainError("INVALID_BINDING", f"Для {logical} требуется совместимый ресурс", 422)
        access["resourceKey"] = physical
    row = query(
        conn,
        """INSERT INTO processes(process_id,version,name,access_templates,handler,description,
                  priority,duration,active,bindings,join_active) VALUES (:code,:v,:name,CAST(:templates AS
                  jsonb),:handler,
                  :description,:priority,:duration,:active,CAST(:bindings AS jsonb),:join_active) RETURNING
                  *""",
        code=code,
        v=expected + 1,
        name=spec.name,
        templates=encode(templates),
        handler=spec.handler,
        description=spec.description,
        priority=spec.priority,
        duration=spec.executionSeconds,
        active=spec.isActive,
        bindings=encode(spec.bindings),
        join_active=spec.joinActive,
    ).one()
    return process_info(row)
