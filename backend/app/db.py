import os
import re
import sqlite3
from .config import DB_PATH

# SQLite remains available for tests and offline use. PostgreSQL is opt-in.
DB_BACKEND = os.getenv('ADA_DATABASE_BACKEND', 'sqlite').lower()
DB_SCHEMA = os.getenv('ADA_DATABASE_SCHEMA', 'ada')

class HybridRow(dict):
    """Mapping like sqlite3.Row, including the existing positional accesses."""
    def __getitem__(self, key):
        if isinstance(key, int): return tuple(self.values())[key]
        return super().__getitem__(key)

def hybrid_row(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: HybridRow(zip(names, values))

class PgConnection:
    def __init__(self, raw): self.raw = raw
    def __enter__(self): return self
    def __exit__(self, typ, value, tb):
        try:
            if typ is None: self.raw.commit()
            else: self.raw.rollback()
        finally: self.raw.close()
    def execute(self, query, params=()):
        # Existing queries use sqlite's placeholders. All SQL is static application SQL.
        return self.raw.execute(query.replace('?', '%s'), params)
    def executemany(self, query, rows):
        with self.raw.cursor() as cur: cur.executemany(query.replace('?', '%s'), rows)

def pg_config():
    import psycopg
    url = os.getenv('SUPABASE_DATABASE_URL', '').strip()
    cert = os.getenv('SUPABASE_DB_CA_CERT', '').strip()
    if not url or not cert: raise RuntimeError('Configure a conexão e o certificado do Supabase no .env.')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,48}', DB_SCHEMA): raise RuntimeError('Nome de esquema inválido.')
    return psycopg, url, cert

def begin_write(conn):
    if isinstance(conn, PgConnection):
        # Serialize approval/version allocation in one transaction, like SQLite BEGIN IMMEDIATE.
        conn.execute('SELECT pg_advisory_xact_lock(21422817)')
    else:
        conn.execute('BEGIN IMMEDIATE')

def insert_id(conn, query, params):
    if isinstance(conn, PgConnection):
        return conn.execute(query + ' RETURNING id', params).fetchone()[0]
    return conn.execute(query, params).lastrowid


def connect():
    if DB_BACKEND == 'postgres':
        psycopg, url, cert = pg_config()
        raw = psycopg.connect(url, sslmode='verify-full', sslrootcert=cert, connect_timeout=10, row_factory=hybrid_row)
        try:
            from psycopg import sql
            raw.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(DB_SCHEMA)))
        except Exception:
            raw.close()
            raise
        return PgConnection(raw)
    if DB_BACKEND != 'sqlite': raise RuntimeError('ADA_DATABASE_BACKEND deve ser sqlite ou postgres.')
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def init_db():
    if DB_BACKEND == 'postgres':
        with connect() as c:
            row = c.execute('SELECT version FROM schema_migrations WHERE version=?', (2,)).fetchone()
            if not row: raise RuntimeError('O esquema PostgreSQL da Ada ainda não foi preparado.')
        return
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY, name TEXT NOT NULL, active_version INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS agent_versions (agent_id INTEGER NOT NULL REFERENCES agents(id), number INTEGER NOT NULL, spec_json TEXT NOT NULL, reason TEXT NOT NULL, origin TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(agent_id,number));
        CREATE TRIGGER IF NOT EXISTS immutable_versions BEFORE UPDATE ON agent_versions BEGIN SELECT RAISE(ABORT, 'agent versions are immutable'); END;
        CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL REFERENCES agents(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS conversation_labels (conversation_id INTEGER PRIMARY KEY REFERENCES conversations(id), title TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id), role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL REFERENCES agents(id), conversation_id INTEGER NOT NULL REFERENCES conversations(id), status TEXT NOT NULL, steps INTEGER NOT NULL DEFAULT 0, tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0, cost_estimate REAL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS run_events (run_id INTEGER NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(run_id,seq));
        CREATE TABLE IF NOT EXISTS approvals (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), tool TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, decided_at TEXT);
        CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL REFERENCES agents(id), name TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE, agent_id INTEGER NOT NULL REFERENCES agents(id), position INTEGER NOT NULL, content TEXT NOT NULL);
        """)
        from .cookbooks import prepare_catalog
        prepare_catalog(c)

