import json
import logging
from datetime import UTC, datetime

logger = logging.getLogger("risk.events")
logger.propagate = False
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())


def record(event: str, **fields) -> None:
    entry = {"time": datetime.now(UTC).isoformat(), "event": event, **fields}
    logger.info(json.dumps(entry, ensure_ascii=False, default=str))
