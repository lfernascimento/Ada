"""Cria o catálogo idempotente no banco configurado; não copia dados de terceiros."""
import sys
from pathlib import Path
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')
sys.path.insert(0, str(ROOT / 'backend'))
from app.db import connect, DB_BACKEND
from app.cookbooks import prepare_catalog

def main():
    try:
        with connect() as c:
            prepare_catalog(c)
            if DB_BACKEND == 'postgres':
                c.execute('ALTER TABLE cookbook_references ENABLE ROW LEVEL SECURITY')
                c.execute('ALTER TABLE cookbook_source_files ENABLE ROW LEVEL SECURITY')
        print('Catálogo de referências preparado no banco configurado.')
        return 0
    except Exception:
        print('Não foi possível preparar o catálogo. A transação foi revertida; confira conexão e permissões.')
        return 1

if __name__ == '__main__':
    sys.exit(main())
