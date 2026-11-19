"""Create the initial Ada PostgreSQL schema explicitly, without touching SQLite."""
import os, re, sys
from pathlib import Path
from dotenv import load_dotenv
import psycopg
from psycopg import sql
ROOT=Path(__file__).resolve().parents[2]
load_dotenv(ROOT/'.env')
MIGRATION=ROOT/'backend'/'migrations'/'001_postgres_draft.sql'
RLS_MIGRATION=ROOT/'backend'/'migrations'/'002_enable_rls.sql'

def upgrade_current(conn,schema):
    sys.path.insert(0,str(ROOT/'backend'))
    from app.cookbooks import prepare_catalog
    with conn.cursor() as cur:
        cur.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
        cur.execute('CREATE TABLE IF NOT EXISTS conversation_labels (conversation_id BIGINT PRIMARY KEY REFERENCES conversations(id), title TEXT NOT NULL)')
        cur.execute((ROOT/'backend'/'migrations'/'003_notes.sql').read_text(encoding='utf-8-sig'),prepare=False)
    from app.db import PgConnection
    prepare_catalog(PgConnection(conn))
    with conn.cursor() as cur:
        cur.execute((ROOT/'backend'/'migrations'/'004_reviews.sql').read_text(encoding='utf-8-sig'),prepare=False)
        cur.execute('INSERT INTO schema_migrations(version) VALUES (4) ON CONFLICT DO NOTHING')
        cur.execute((ROOT/'backend'/'migrations'/'005_context.sql').read_text(encoding='utf-8-sig'),prepare=False)
        cur.execute('INSERT INTO schema_migrations(version) VALUES (5) ON CONFLICT DO NOTHING')
        cur.execute((ROOT/'backend'/'migrations'/'006_workflows.sql').read_text(encoding='utf-8-sig'),prepare=False)
        cur.execute('INSERT INTO schema_migrations(version) VALUES (6) ON CONFLICT DO NOTHING')
        cur.execute((ROOT/'backend'/'migrations'/'007_attachments.sql').read_text(encoding='utf-8-sig'),prepare=False)
        cur.execute('INSERT INTO schema_migrations(version) VALUES (7) ON CONFLICT DO NOTHING')
    with conn.cursor() as cur:
        cur.execute((ROOT/'backend'/'migrations'/'008_sdk_runtime.sql').read_text(encoding='utf-8-sig'),prepare=False)
        cur.execute('INSERT INTO schema_migrations(version) VALUES (8) ON CONFLICT DO NOTHING')
    with conn.cursor() as cur:
        for table in ('conversation_labels','cookbook_references','cookbook_source_files'):
            cur.execute(sql.SQL('ALTER TABLE {} ENABLE ROW LEVEL SECURITY').format(sql.Identifier(table)))
        cur.execute('INSERT INTO schema_migrations(version) VALUES (3) ON CONFLICT DO NOTHING')

def prepare(schema='ada'):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,48}',schema): raise ValueError('Nome de esquema inválido.')
    url=os.getenv('SUPABASE_DATABASE_URL',''); cert=os.getenv('SUPABASE_DB_CA_CERT','')
    if not url or not cert or not Path(cert).is_file(): raise RuntimeError('Conexão ou certificado não configurados.')
    migration=MIGRATION.read_text(encoding='utf-8-sig')
    migration=migration.replace('BEGIN;\n','',1).replace('\nCOMMIT;','')
    migration=migration.replace('CREATE SCHEMA IF NOT EXISTS ada;',f'CREATE SCHEMA {schema};')
    migration=migration.replace('SCHEMA ada ',f'SCHEMA {schema} ').replace('ada.',f'{schema}.')
    with psycopg.connect(url,sslmode='verify-full',sslrootcert=cert,connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=%s)',(schema,))
            if cur.fetchone()[0]:
                cur.execute('SELECT to_regclass(%s)',(schema+'.schema_migrations',))
                if not cur.fetchone()[0]: raise RuntimeError('Esquema existente não pertence à migração da Ada; nada foi alterado.')
                cur.execute(sql.SQL('SELECT version FROM {}.schema_migrations WHERE version=1').format(sql.Identifier(schema)))
                if not cur.fetchone(): raise RuntimeError('Versão do esquema não reconhecida; nada foi alterado.')
                cur.execute(sql.SQL('SELECT version FROM {}.schema_migrations WHERE version=2').format(sql.Identifier(schema)))
                if not cur.fetchone():
                    cur.execute(RLS_MIGRATION.read_text(encoding='utf-8-sig').replace('ada.',schema+'.'),prepare=False)
                    cur.execute(sql.SQL('INSERT INTO {}.schema_migrations(version) VALUES (2)').format(sql.Identifier(schema)))
                upgrade_current(conn,schema)
                return False
            cur.execute(migration,prepare=False)
            cur.execute(sql.SQL('CREATE TABLE {}.schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())').format(sql.Identifier(schema)))
            cur.execute(sql.SQL('INSERT INTO {}.schema_migrations(version) VALUES (1)').format(sql.Identifier(schema)))
            cur.execute(RLS_MIGRATION.read_text(encoding='utf-8-sig').replace('ada.',schema+'.'),prepare=False)
            cur.execute(sql.SQL('INSERT INTO {}.schema_migrations(version) VALUES (2)').format(sql.Identifier(schema)))
        upgrade_current(conn,schema)
    return True

def main():
    try:
        created=prepare()
        print('Esquema PostgreSQL da Ada criado.' if created else 'Esquema PostgreSQL da Ada verificado e atualizado.')
        print('SQLite não foi alterado. Nenhum registro foi transferido.')
        return 0
    except (psycopg.Error,ValueError,RuntimeError,OSError):
        print('Não foi possível preparar o esquema. A operação foi revertida; confira configuração e permissões.')
        return 1
if __name__=='__main__':sys.exit(main())
