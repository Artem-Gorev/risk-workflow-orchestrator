from collections import Counter, defaultdict

from backend.orchestrator.resources import conflicts
from backend.orchestrator.storage import query


def dependencies(conn):
    rows = query(
        conn,
        "SELECT DISTINCT ON(process_id) * FROM processes ORDER BY process_id,version DESC",
    ).all()
    resources = list(
        query(
            conn,
            "SELECT resource_key,partitioning,kind FROM resources ORDER BY resource_key",
        ).mappings()
    )
    owners = list(query(conn, "SELECT * FROM reservations ORDER BY resource_key,job_id").mappings())
    waiting = list(
        query(
            conn,
            "SELECT id,process,reason FROM jobs WHERE status='WAITING' ORDER BY seq",
        ).mappings()
    )
    return {
        "resources": resources,
        "owners": owners,
        "activeProcesses": {
            str(r.id): r.process
            for r in query(
                conn,
                "SELECT id,process FROM jobs WHERE status IN ('STARTING','RUNNING')",
            )
        },
        "waiting": waiting,
        "processes": [
            {
                "processId": r.process_id,
                "accesses": r.access_templates,
                "isActive": r.active,
            }
            for r in rows
        ],
    }


def report(conn):
    rows = query(
        conn,
        """WITH periods AS (
        SELECT job_id,type,payload,occurred,lead(occurred,1,now()) OVER(PARTITION BY job_id ORDER BY id) AS
        until
        FROM job_events WHERE type<>'JOB_TRIGGER_JOINED')
        SELECT jobs.process,periods.job_id,payload,extract(epoch FROM until-greatest(occurred,now()-interval
        '7 days')) AS seconds
        FROM periods JOIN jobs ON jobs.id=periods.job_id WHERE type IN ('JOB_WAITING',
        'JOB_WAIT_REASON_CHANGED')
        AND until>now()-interval '7 days'""",
    ).all()
    delays, affected = Counter(), defaultdict(set)
    total_wait = 0
    for row in rows:
        duration = max(0, float(row.seconds))
        total_wait += duration
        reason = row.payload["waitReason"]
        if reason["code"] != "RESOURCE_CONFLICT":
            continue
        keys = {c["resourceKey"] for c in reason["conflicts"]}
        affected[row.process].add(row.job_id)
        for key in keys:
            delays[key] += duration / len(keys)
    samples = list(
        query(
            conn,
            """SELECT process,count(*) AS jobs,count(*) FILTER(WHERE status='FAILED') AS failures,
        avg(extract(epoch FROM finished-started)) FILTER(WHERE finished IS NOT NULL) AS runtime,
        count(*) FILTER(WHERE EXISTS(SELECT 1 FROM job_events e WHERE e.job_id=j.id AND
        e.payload->'waitReason'->>'code'='RESOURCE_CONFLICT')) AS blocked FROM jobs j
        WHERE queued>=now()-interval '7 days' GROUP BY process ORDER BY process""",
        ).mappings()
    )
    totals = query(
        conn,
        """SELECT count(*) AS total, avg(extract(epoch FROM finished-started)) AS average,
        count(*) FILTER(WHERE status='FAILED') AS failures FROM jobs""",
    ).one()
    load = list(
        query(
            conn,
            """SELECT t AS minute,coalesce(sum(extract(epoch FROM least(coalesce(j.finished,now()),t+interval
                '1 minute',now())
        -greatest(j.started,t))) FILTER(WHERE j.id IS NOT NULL),0) AS "busySeconds"
        FROM generate_series(date_trunc('minute',now())-interval '59 minutes',date_trunc('minute',now()),
        interval '1 minute') t
        LEFT JOIN jobs j ON j.started<t+interval '1 minute' AND coalesce(j.finished,now())>t
        GROUP BY t ORDER BY t""",
        ).mappings()
    )
    recent = list(
        query(
            conn,
            "SELECT id,job_id,type,occurred FROM job_events ORDER BY id DESC LIMIT 15",
        ).mappings()
    )
    hotspots = [
        {
            "resourceKey": k,
            "waitSeconds": round(v, 3),
            "shareOfWait": v / total_wait if total_wait else 0,
        }
        for k, v in delays.most_common()
    ]
    return {
        "days": 7,
        "totalJobs": totals.total,
        "failedJobs": totals.failures,
        "meanRuntimeSeconds": totals.average,
        "waitSeconds": round(total_wait, 3),
        "resources": hotspots,
        "processes": samples,
        "load": load,
        "recentEvents": recent,
        "conflictedJobs": sum(map(len, affected.values())),
    }


def forecast(conn, definition, day):
    accesses = [
        {
            "resourceKey": a["resourceKey"],
            "mode": a["mode"],
            "scope": {"type": "GLOBAL"} if a["scope"] == "GLOBAL" else {"type": "DATE", "date": str(day)},
        }
        for a in definition.access_templates
    ]
    owners = [
        {
            "resourceKey": r.resource_key,
            "scope": r.scope,
            "mode": r.mode,
            "jobId": str(r.job_id),
        }
        for r in query(conn, "SELECT * FROM reservations ORDER BY job_id,resource_key")
    ]
    historical = query(
        conn,
        """SELECT count(*) AS n,count(*) FILTER(WHERE EXISTS(SELECT 1 FROM job_events e WHERE e.job_id=j.id
        AND e.payload->'waitReason'->>'code'='RESOURCE_CONFLICT')) AS blocked FROM jobs j
        WHERE process=:p AND queued>=now()-interval '7 days'""",
        p=definition.process_id,
    ).one()
    return {
        "conflictsNow": conflicts(accesses, owners),
        "samples": historical.n,
        "historicalConflictRate": historical.blocked / historical.n if historical.n else None,
    }
