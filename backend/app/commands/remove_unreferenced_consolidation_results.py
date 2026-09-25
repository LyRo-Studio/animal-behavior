"""One-off deploy step for ticket #149: remove every stored consolidation
result that no Consolidation references — the results of the old-format
consolidations migration 0016 deleted. Re-runnable. Run after
`alembic upgrade head`, inside the backend container:

    python -m app.commands.remove_unreferenced_consolidation_results

See app.services.consolidation.remove_unreferenced_consolidation_results.
"""

import logging

from app.db.session import SessionLocal
from app.services.consolidation import remove_unreferenced_consolidation_results
from app.services.s3_client import get_s3_client


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    s3 = get_s3_client()
    db = SessionLocal()
    try:
        removed = remove_unreferenced_consolidation_results(db, s3=s3)
    finally:
        db.close()
    print(f"Removed {len(removed)} unreferenced consolidation result(s).")


if __name__ == "__main__":
    main()
