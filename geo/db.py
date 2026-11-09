from contextlib import contextmanager

import mysql.connector

from .config import Settings


class DatabaseUnavailable(RuntimeError):
    """Raised when the configured server cannot be reached or authenticated."""


def connect(settings: Settings, include_database: bool = True):
    options = {
        "host": settings.db_host,
        "port": settings.db_port,
        "user": settings.db_user,
        "password": settings.db_password,
        "connection_timeout": settings.db_connect_timeout,
        "autocommit": False,
        "use_unicode": True,
        "charset": "utf8mb4",
    }
    if include_database:
        options["database"] = settings.db_name
    if settings.db_ssl_ca:
        options["ssl_ca"] = settings.db_ssl_ca
        options["ssl_verify_cert"] = settings.db_ssl_verify_cert
    try:
        return mysql.connector.connect(**options)
    except mysql.connector.Error as exc:
        raise DatabaseUnavailable(
            f"Cannot connect to MySQL/MariaDB at {settings.db_host}:{settings.db_port} "
            f"for database '{settings.db_name}'. Start the database service and verify .env. "
            f"Server message: {exc}"
        ) from exc


@contextmanager
def cursor(settings: Settings, dictionary: bool = True, include_database: bool = True):
    connection = connect(settings, include_database=include_database)
    cur = connection.cursor(dictionary=dictionary)
    try:
        yield connection, cur
    finally:
        cur.close()
        connection.close()

