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
                return False
            cur.execute(migration,prepare=False)
            cur.execute(sql.SQL('CREATE TABLE {}.schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())').format(sql.Identifier(schema)))
            cur.execute(sql.SQL('INSERT INTO {}.schema_migrations(version) VALUES (1)').format(sql.Identifier(schema)))
            cur.execute(RLS_MIGRATION.read_text(encoding='utf-8-sig').replace('ada.',schema+'.'),prepare=False)
            cur.execute(sql.SQL('INSERT INTO {}.schema_migrations(version) VALUES (2)').format(sql.Identifier(schema)))
    return True

def main():
    try:
        created=prepare()
        print('Esquema PostgreSQL da Ada criado.' if created else 'Esquema PostgreSQL da Ada já estava preparado.')
        print('SQLite não foi alterado. Nenhum registro foi transferido.')
        return 0
    except (psycopg.Error,ValueError,RuntimeError,OSError):
        print('Não foi possível preparar o esquema. A operação foi revertida; confira configuração e permissões.')
        return 1
if __name__=='__main__':sys.exit(main())
