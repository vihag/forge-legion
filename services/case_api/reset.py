"""Drop and recreate the case store tables.

Usage:
    python -m services.case_api.reset

The database path is CASE_DB_PATH, or ./data/cases.db when that variable is unset.
"""

import os

from services.case_api.store import Store


def main() -> None:
    db_path = os.environ.get("CASE_DB_PATH", "./data/cases.db")
    store = Store(db_path)
    try:
        store.reset()
    finally:
        store.close()


if __name__ == "__main__":
    main()
