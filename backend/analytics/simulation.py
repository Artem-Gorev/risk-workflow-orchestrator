from math import ceil
from random import Random

from backend.orchestrator.resources import conflicts


def simulate(seed, count, slots):
    rng = Random(seed)
    requests = []
    for i in range(count):
        resource = rng.choice(["exposures", "positions", "cash"])
        mode = rng.choice(["READ", "WRITE"])
        requests.append(
            {
                "id": str(i),
                "process": f"{mode.lower()}-{resource}",
                "user": f"analyst-{rng.randrange(4)}",
                "arrival": i // 4,
                "duration": rng.randint(2, 6),
                "access": {
                    "resourceKey": resource,
                    "scope": {"type": "DATE", "date": "D1"},
                    "mode": mode,
                },
            }
        )

    def run(policy):
        pending = requests.copy()
        active = []
        violations = 0
        waits = []
        clock = 0
        while pending or active:
            active = [(end, task) for end, task in active if end > clock]
            for task in pending.copy():
                if len(active) >= slots or task["arrival"] > clock:
                    break
                owners = [dict(other["access"], jobId=other["id"]) for _, other in active]
                blocked = conflicts([task["access"]], owners)
                if blocked and policy != "unmanaged":
                    if policy == "fifo":
                        break
                    continue
                violations += len(blocked)
                waits.append(clock - task["arrival"])
                active.append((clock + task["duration"], task))
                pending.remove(task)
            if active or pending:
                clock += 1
        return {
            "durationTicks": clock,
            "conflictingPairs": violations,
            "queueTicks": sum(waits),
            "p95WaitTicks": sorted(waits)[ceil(0.95 * len(waits)) - 1],
        }

    return {
        "seed": seed,
        "requests": requests,
        "fifo": run("fifo"),
        "managed": run("managed"),
        "unmanaged": run("unmanaged"),
    }
