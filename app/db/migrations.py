from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection

SCRIPT_LOCATION = Path(__file__).with_name("alembic")


def run_migrations(connection: Connection) -> None:
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    config.attributes["connection"] = connection
    if connection.dialect.name != "sqlite":
        command.upgrade(config, "head")
        return

    if connection.in_transaction():
        raise RuntimeError("SQLite migrations require a connection without a transaction.")
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    connection.commit()
    try:
        command.upgrade(config, "head")
        if connection.in_transaction():
            connection.commit()
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"SQLite foreign key violations after migration: {violations}")
    finally:
        if connection.in_transaction():
            connection.rollback()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
