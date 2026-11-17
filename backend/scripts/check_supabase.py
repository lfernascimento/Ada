"""Read-only connectivity check; never prints the connection URL or secrets."""
import os, sqlite3, sys
from pathlib import Path
from dotenv import load_dotenv
import psycopg
from psycopg.conninfo import conninfo_to_dict
ROOT=Path(__file__).resolve().parents[2]
load_dotenv(ROOT/'.env')

def main():
    url=os.environ.get('SUPABASE_DATABASE_URL','').strip()
    ca=os.environ.get('SUPABASE_DB_CA_CERT','').strip()
    if not url:
        print('Pendente: configure SUPABASE_DATABASE_URL no .env local.'); return 2
    if not ca or not Path(ca).is_file():
        print('Pendente: informe SUPABASE_DB_CA_CERT com o caminho do certificado CA baixado do Supabase.'); return 2
    try:
        params=conninfo_to_dict(url)
        if not params.get('host'): raise ValueError('Host ausente')
        # Explicit kwargs override a weaker sslmode in the URL.
        with psycopg.connect(url,sslmode='verify-full',sslrootcert=ca,connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute('BEGIN TRANSACTION READ ONLY')
                cur.execute('SELECT current_setting(%s)',('server_version',))
                version=cur.fetchone()[0]
                cur.execute('SELECT current_setting(%s)',('transaction_read_only',))
                if cur.fetchone()[0]!='on': raise RuntimeError('Transação não é somente leitura')
                conn.rollback()
        print('Conexão com PostgreSQL estabelecida, certificado e nome do servidor verificados.')
        print('Versão PostgreSQL:',version)
    except (psycopg.Error,ValueError,RuntimeError,TypeError):
        # Driver messages can include connection details; never print them.
        print('Não foi possível verificar a conexão. Confira URL, senha, certificado CA, rede e modo Session pooler no Supabase.')
        return 1
    local=ROOT/'ada.db'
    if local.exists():
        with sqlite3.connect(f'file:{local.as_posix()}?mode=ro',uri=True) as c:
            names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('agents','agent_versions','conversations','messages','runs','documents'):
                if table in names:
                    count=c.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0]
                    print(f'Banco local: {table} = {count} registros.')
    print('Verificação concluída. Nenhuma tabela foi criada ou alterada e nenhum dado foi migrado.')
    return 0
if __name__=='__main__': sys.exit(main())
