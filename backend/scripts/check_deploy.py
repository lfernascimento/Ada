"""Read-only deployment preflight. Never prints credentials or changes the database."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def inspect(values, cloud=False):
    checks = []

    def add(name, passed):
        checks.append({'check': name, 'status': 'passed' if passed else 'missing'})

    add('Frontend compilado', (ROOT / 'frontend/dist/index.html').is_file())
    add('Provedor de modelo selecionado', values.get('ADA_MODEL_PROVIDER') in ('claude_agent_sdk', 'openai_compatible'))
    add('Modelo e chave presentes (sem validar saldo)', bool(values.get('MODEL_NAME') and values.get('MODEL_API_KEY')))
    if cloud:
        origin = (values.get('ADA_PUBLIC_ORIGIN') or '').rstrip('/')
        parsed = urlsplit(origin)
        valid_origin = parsed.scheme == 'https' and bool(parsed.hostname) and not any((parsed.path, parsed.query, parsed.fragment, parsed.username))
        add('Modo privado hospedado', values.get('ADA_DEPLOYMENT_MODE') == 'private_cloud')
        add('Origem HTTPS sem caminho', valid_origin)
        add('Retorno OAuth na mesma origem', valid_origin and (values.get('ADA_OAUTH_CALLBACK_BASE') or '').rstrip('/') == origin)
        add('Autenticação privada configurada', bool(values.get('ADA_ACCESS_USER', 'ada')) and len(values.get('ADA_ACCESS_PASSWORD') or '') >= 24)
        add('Banco PostgreSQL selecionado', values.get('ADA_DATABASE_BACKEND') == 'postgres')
        add('Conexão PostgreSQL presente', bool(values.get('SUPABASE_DATABASE_URL')))
        add('Certificado CA presente neste ambiente', Path(values.get('SUPABASE_DB_CA_CERT') or '__missing__').is_file())
    return checks


def database_check(values):
    """Uses only SELECT, a read-only transaction and a bounded connection timeout."""
    try:
        import psycopg
        from psycopg import sql
        schema = values.get('ADA_DATABASE_SCHEMA') or 'ada'
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,48}', schema):
            raise ValueError('invalid schema')
        with psycopg.connect(values.get('SUPABASE_DATABASE_URL') or '', sslmode='verify-full',
                              sslrootcert=values.get('SUPABASE_DB_CA_CERT') or '', connect_timeout=10) as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            conn.execute("SET LOCAL statement_timeout = '10s'")
            version = conn.execute(sql.SQL('SELECT version FROM {}.schema_migrations WHERE version=7').format(sql.Identifier(schema))).fetchone()
            return {'check': 'Banco acessível com esquema 7 e TLS verificado', 'status': 'passed' if version else 'missing'}
    except Exception:
        # Driver errors can include hostnames, usernames or connection strings.
        return {'check': 'Banco acessível com esquema 7 e TLS verificado', 'status': 'missing'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--cloud', action='store_true')
    parser.add_argument('--database', action='store_true', help='Executa SELECT no PostgreSQL; não aplica migrações.')
    args = parser.parse_args()
    values = dotenv_values(args.env_file) if args.env_file.is_file() else {}
    checks = inspect(values, args.cloud)
    if args.database:
        checks.append(database_check(values))
    print(json.dumps({'checks': checks, 'note': 'Não valida saldo, OAuth, executor remoto nem publicação real.'}, ensure_ascii=False, indent=2))
    return int(any(item['status'] != 'passed' for item in checks))


if __name__ == '__main__':
    raise SystemExit(main())
