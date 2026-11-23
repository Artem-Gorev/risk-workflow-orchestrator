import argparse
import os
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from backend.orchestrator.storage import Storage, query
from backend.workers.catalog import PROCESSES
from backend.workers.synthetic import in_synthetic_period, period_label


def main():
    parser = argparse.ArgumentParser(description="Добавить одноразовое срабатывание планировщика")
    parser.add_argument("--process", choices=sorted(PROCESSES), default="credit-risk")
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--in-seconds", type=int, default=10)
    args = parser.parse_args()
    if args.in_seconds < 0:
        parser.error("Задержка должна быть неотрицательной")
    if not in_synthetic_period(args.date):
        parser.error(f"Выберите расчётную дату за {period_label()} включительно.")
    storage = Storage(os.environ["DATABASE_URL"])
    schedule_id = f"once-{uuid4()}"
    due = datetime.now(UTC) + timedelta(seconds=args.in_seconds)
    try:
        with storage.transaction() as conn:
            query(
                conn,
                "INSERT INTO schedules(id,process,day,due) VALUES (:id,:process,:day,:due)",
                id=schedule_id,
                process=args.process,
                day=args.date,
                due=due,
            )
    finally:
        storage.engine.dispose()
    print(schedule_id, due.isoformat())


if __name__ == "__main__":
    main()
