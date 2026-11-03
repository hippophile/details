"""PostgreSQL access (psycopg 3). Server code uses the async pool; the migrate CLI and tests use sync connect().
Connection errors are re-raised with the DSN and its password redacted."""
import urllib.parse

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


class DatabaseError(RuntimeError):
    pass


def redact(text, url):
    """Remove the DSN and its password from text."""
    text = text.replace(url, "<dsn>")
    try:
        pw = urllib.parse.urlsplit(url).password
    except ValueError:
        pw = None
    if pw:
        text = text.replace(pw, "***").replace(urllib.parse.quote(pw, safe=""), "***")
    return text


def connect(url, timeout=5):
    """Sync autocommit connection (use conn.transaction() for atomic blocks)."""
    try:
        return psycopg.connect(url, autocommit=True, connect_timeout=timeout)
    except Exception as e:
        raise DatabaseError(redact(f"database connection failed: {e}", url)) from None


async def open_pool(url, timeout=5):
    pool = AsyncConnectionPool(url, open=False, min_size=1, max_size=5, timeout=timeout,
                               kwargs={"row_factory": dict_row, "connect_timeout": timeout})
    try:
        await pool.open(wait=True, timeout=timeout)
    except Exception as e:
        await pool.close()
        raise DatabaseError(redact(f"database connection failed: {type(e).__name__}: {e}", url)) from None
    return pool
