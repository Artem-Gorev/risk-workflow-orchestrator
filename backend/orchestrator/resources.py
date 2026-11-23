from collections import defaultdict


def conflicts(requested, active):
    owners_by_resource = defaultdict(list)
    for owner in active:
        owners_by_resource[owner["resourceKey"]].append(owner)

    blocked = []
    reported = set()
    for access in requested:
        resource = access["resourceKey"]
        for owner in owners_by_resource.get(resource, ()):
            if owner["mode"] == access["mode"] == "READ":
                continue
            left, right = access["scope"], owner["scope"]
            if left["type"] == right["type"] == "DATE" and left["date"] != right["date"]:
                continue
            scope = right if left["type"] == "GLOBAL" else left
            key = (resource, scope["type"], scope.get("date"), access["mode"], owner["mode"], owner["jobId"])
            if key in reported:
                continue
            reported.add(key)
            blocked.append(
                {
                    "resourceKey": resource,
                    "scope": scope,
                    "requestedMode": access["mode"],
                    "activeMode": owner["mode"],
                    "blockingJobId": owner["jobId"],
                }
            )
    return blocked
