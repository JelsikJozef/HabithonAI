"""PostgreSQL-backed token vault for reversible token mapping.

Schema (expected):

CREATE TABLE IF NOT EXISTS {table} (
    tenant_id   text NOT NULL,
    token_id    text NOT NULL,
    pii_type    text NOT NULL,
    value_enc   bytea NOT NULL,
    first_seen  timestamptz NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, token_id)
);

API:
- get_token(tenant_id: str | None) -> str
- upsert(token_id: str, tenant_id: str | None, pii_type: str, value: str) -> None
- lookup(token_id: str, tenant_id: str | None) -> str | None

Notes:
- Uses parameterized SQL; safe for concurrent inserts via ON CONFLICT DO NOTHING.
- Lazy import of psycopg to keep module importable without the driver.
- Treats tenant_id as part of identity; None maps to empty string.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
import sqlite3

from ...domain.entities import TokenMapping
from ...domain.errors import TokenVaultError
from ...domain.ports import TokenVaultPort


def _norm_tenant(tenant_id: str | None) -> str:
    return tenant_id or ""


@dataclass
class PostgresTokenVault(TokenVaultPort):
    dsn: str | None = None
    table: str = "pii_tokens"
    conn_factory: Callable[[], Any] | None = None  # returns a DB-API connection
    # Sticky connection for sqlite (e.g., :memory:) to preserve schema across calls
    _sticky_conn: Any | None = None

    def _get_conn(self):
        if self.conn_factory is not None:
            # Reuse sticky sqlite connection if available
            if self._sticky_conn is not None:
                return self._sticky_conn
            conn = self.conn_factory()
            # Keep sqlite connections open to preserve :memory: state across calls
            if isinstance(conn, sqlite3.Connection):
                self._sticky_conn = conn
            return conn
        if not self.dsn:
            raise RuntimeError("PostgresTokenVault requires either conn_factory or dsn")
        # Lazy import psycopg (v3)
        try:
            import psycopg
        except Exception as e:
            raise RuntimeError("psycopg driver not installed") from e
        return psycopg.connect(self.dsn)

    def _should_close(self, conn: Any) -> bool:
        # Do not close sticky sqlite connection
        return not (isinstance(conn, sqlite3.Connection) and conn is self._sticky_conn)

    def ensure_schema(self) -> None:
        # Create schema for sqlite or postgres
        conn = self._get_conn()
        try:
            if isinstance(conn, sqlite3.Connection):
                create_sql = f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    tenant_id text NOT NULL,
                    token_id text NOT NULL,
                    pii_type text NOT NULL,
                    value_enc blob NOT NULL,
                    first_seen timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (tenant_id, token_id)
                );
                """
                cur = conn.cursor()
                cur.execute(create_sql)
                conn.commit()
                cur.close()
            else:
                create_sql = f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    tenant_id   text NOT NULL,
                    token_id    text NOT NULL,
                    pii_type    text NOT NULL,
                    value_enc   bytea NOT NULL,
                    first_seen  timestamptz NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (tenant_id, token_id)
                );
                """
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(create_sql)
        finally:
            try:
                if self._should_close(conn):
                    conn.close()
            except Exception:
                pass

    # Optional namespace accessor
    def get_token(self, tenant_id: str | None = None) -> str:
        return _norm_tenant(tenant_id)

    def upsert(self, token_id: str, tenant_id: str | None, pii_type: str, value: str) -> None:
        conn = self._get_conn()
        try:
            if isinstance(conn, sqlite3.Connection):
                sql = f"INSERT OR IGNORE INTO {self.table} (tenant_id, token_id, pii_type, value_enc, first_seen) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)"
                cur = conn.cursor()
                cur.execute(
                    sql, (_norm_tenant(tenant_id), token_id, pii_type, value.encode("utf-8"))
                )
                conn.commit()
                cur.close()
            else:
                sql = f"""
        INSERT INTO {self.table} (tenant_id, token_id, pii_type, value_enc, first_seen)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (tenant_id, token_id) DO NOTHING
        """
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            sql,
                            (_norm_tenant(tenant_id), token_id, pii_type, value.encode("utf-8")),
                        )
        finally:
            try:
                if self._should_close(conn):
                    conn.close()
            except Exception:
                pass

    def lookup(self, token_id: str, tenant_id: str | None) -> str | None:
        conn = self._get_conn()
        try:
            if isinstance(conn, sqlite3.Connection):
                sql = f"SELECT value_enc FROM {self.table} WHERE tenant_id = ? AND token_id = ?"
                cur = conn.cursor()
                cur.execute(sql, (_norm_tenant(tenant_id), token_id))
                row = cur.fetchone()
                cur.close()
            else:
                sql = f"""
        SELECT value_enc FROM {self.table}
        WHERE tenant_id = %s AND token_id = %s
        """
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(sql, (_norm_tenant(tenant_id), token_id))
                        row = cur.fetchone()
            if not row:
                return None
            val_bytes = row[0]
            if isinstance(val_bytes, memoryview):
                val_bytes = val_bytes.tobytes()
            if isinstance(val_bytes, bytes):
                return val_bytes.decode("utf-8")
            # Some drivers may return str already
            return str(val_bytes)
        finally:
            try:
                if self._should_close(conn):
                    conn.close()
            except Exception:
                pass

    # Implement TokenVaultPort interface
    def save_mappings(self, context_id: str, mappings: Iterable[TokenMapping]) -> None:
        try:
            for m in mappings:
                self.upsert(m.token, context_id, m.type, m.value)
        except Exception as e:
            raise TokenVaultError(f"Failed to save mappings for {context_id}: {e}")

    def get_mappings(self, context_id: str) -> list[TokenMapping]:
        conn = self._get_conn()
        try:
            if isinstance(conn, sqlite3.Connection):
                sql = f"SELECT token_id, pii_type, value_enc FROM {self.table} WHERE tenant_id = ?"
                cur = conn.cursor()
                cur.execute(sql, (context_id,))
                rows = cur.fetchall()
                cur.close()
            else:
                sql = f"SELECT token_id, pii_type, value_enc FROM {self.table} WHERE tenant_id = %s"
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(sql, (context_id,))
                        rows = cur.fetchall()
            mappings: list[TokenMapping] = []
            for token_id, pii_type, val_bytes in rows:
                if isinstance(val_bytes, memoryview):
                    val_bytes = val_bytes.tobytes()
                if isinstance(val_bytes, bytes):
                    value = val_bytes.decode("utf-8")
                else:
                    value = str(val_bytes)
                mappings.append(TokenMapping(token=token_id, type=pii_type, value=value))
            return mappings
        except Exception as e:
            raise TokenVaultError(f"Failed to load mappings for {context_id}: {e}")
        finally:
            try:
                if self._should_close(conn):
                    conn.close()
            except Exception:
                pass

    def clear_context(self, context_id: str) -> None:
        conn = self._get_conn()
        try:
            if isinstance(conn, sqlite3.Connection):
                sql = f"DELETE FROM {self.table} WHERE tenant_id = ?"
                cur = conn.cursor()
                cur.execute(sql, (context_id,))
                conn.commit()
                cur.close()
            else:
                sql = f"DELETE FROM {self.table} WHERE tenant_id = %s"
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(sql, (context_id,))
        except Exception as e:
            raise TokenVaultError(f"Failed to clear context {context_id}: {e}")
        finally:
            try:
                if self._should_close(conn):
                    conn.close()
            except Exception:
                pass
