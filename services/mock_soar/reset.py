"""Reset Mock SOAR submissions."""

import os

from services.mock_soar.store import SoarStore


def main() -> None:
    db_path = os.environ.get("SOAR_DB_PATH", "./data/soar.db")
    store = SoarStore(db_path)
    try:
        store.reset()
    finally:
        store.close()


if __name__ == "__main__":
    main()