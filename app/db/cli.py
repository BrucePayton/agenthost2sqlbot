from __future__ import annotations

import asyncio

from app.config import Settings
from app.db.base import Database


async def migrate(settings: Settings) -> None:
    database = Database(settings.resolved_database_url)
    try:
        await database.initialize()
    finally:
        await database.dispose()


def main() -> None:
    asyncio.run(migrate(Settings()))


if __name__ == "__main__":
    main()
