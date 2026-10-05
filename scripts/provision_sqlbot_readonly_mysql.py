#!/usr/bin/env python3
"""Provision the POC SQLBot account without persisting administrator credentials."""

from __future__ import annotations

import argparse
import getpass
import secrets
import string

import pymysql


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a SELECT-only SQLBot user for knowledge.sync_job."
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--admin-user", required=True)
    parser.add_argument("--readonly-user", default="sqlbot_poc_ro")
    parser.add_argument(
        "--allowed-host",
        default="%",
        help="MySQL host pattern; restrict this to the SQLBot egress address in production.",
    )
    return parser.parse_args()


def quote_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def random_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "-_!@"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main() -> None:
    args = parse_args()
    admin_password = getpass.getpass("MySQL administrator password: ")
    readonly_password = random_password()
    account = f"'{args.readonly_user.replace(chr(39), chr(39) * 2)}'@'{args.allowed_host.replace(chr(39), chr(39) * 2)}'"
    connection = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.admin_user,
        password=admin_password,
        database="knowledge",
        autocommit=False,
        connect_timeout=10,
        read_timeout=10,
        write_timeout=10,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE USER IF NOT EXISTS {account} IDENTIFIED BY %s", (readonly_password,))
            cursor.execute(f"ALTER USER {account} IDENTIFIED BY %s", (readonly_password,))
            cursor.execute(
                f"GRANT SELECT ON {quote_identifier('knowledge')}.{quote_identifier('sync_job')} TO {account}"
            )
            cursor.execute(f"SHOW GRANTS FOR {account}")
            grants = [str(row[0]) for row in cursor.fetchall()]
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    if not any("SELECT" in grant and "sync_job" in grant for grant in grants):
        raise RuntimeError("The expected table-level SELECT grant was not observed.")
    print("Provisioned a dedicated SELECT-only account for knowledge.sync_job.")
    print(f"MYSQL_READONLY_USER={args.readonly_user}")
    print(f"MYSQL_READONLY_PASSWORD={readonly_password}")
    print("Store the generated password in the Host secret manager, then clear this terminal output.")


if __name__ == "__main__":
    main()
