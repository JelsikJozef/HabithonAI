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

from dataclasses import dataclass
from typing import Optional, Callable, Any


def _norm_tenant(tenant_id: Optional[str]) -> str:
    return tenant_id or ""


@dataclass
class PostgresTokenVault:
    dsn: Optional[str] = None
    table: str = "pii_tokens"
    conn_factory: Optional[Callable[[], Any]] = None  # returns a DB-API connection

    def _get_conn(self):
        if self.conn_factory is not None:
            return self.conn_factory()
        if not self.dsn:
            raise RuntimeError("PostgresTokenVault requires either conn_factory or dsn")
        # Lazy import psycopg (v3)
        try:
            import psycopg
        except Exception as e:
            raise RuntimeError("psycopg driver not installed") from e
        return psycopg.connect(self.dsn)

    def ensure_schema(self) -> None:
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.table} (
            tenant_id   text NOT NULL,
            token_id    text NOT NULL,
            pii_type    text NOT NULL,
            value_enc   bytea NOT NULL,
            first_seen  timestamptz NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, token_id)
        );
        """
        conn = self._get_conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # Optional namespace accessor
    def get_token(self, tenant_id: Optional[str] = None) -> str:
        return _norm_tenant(tenant_id)

    def upsert(self, token_id: str, tenant_id: Optional[str], pii_type: str, value: str) -> None:
        sql = f"""
        INSERT INTO {self.table} (tenant_id, token_id, pii_type, value_enc, first_seen)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (tenant_id, token_id) DO NOTHING
        """
        conn = self._get_conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (_norm_tenant(tenant_id), token_id, pii_type, value.encode("utf-8")))
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def lookup(self, token_id: str, tenant_id: Optional[str]) -> Optional[str]:
        sql = f"""
        SELECT value_enc FROM {self.table}
        WHERE tenant_id = %s AND token_id = %s
        """
        conn = self._get_conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (_norm_tenant(tenant_id), token_id))
                    row = cur.fetchone()
                    if not row:
                        return None
                    val_bytes = row[0]
                    if isinstance(val_bytes, memoryview):
                        val_bytes = val_bytes.tobytes()
                    return val_bytes.decode("utf-8")
        finally:
            try:
                conn.close()
            except Exception:
                pass

