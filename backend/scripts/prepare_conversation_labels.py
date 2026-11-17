"""Cria tabela de títulos sem alterar conversas existentes."""
import sys
from pathlib import Path
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[2]
load_dotenv(ROOT/'.env');sys.path.insert(0,str(ROOT/'backend'))
from app.db import connect,DB_BACKEND
try:
    with connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS conversation_labels (conversation_id INTEGER PRIMARY KEY REFERENCES conversations(id), title TEXT NOT NULL)')
        if DB_BACKEND=='postgres': c.execute('ALTER TABLE conversation_labels ENABLE ROW LEVEL SECURITY')
    print('Títulos de conversas preparados.')
except Exception:
    print('Não foi possível preparar títulos; a transação foi revertida.')
    sys.exit(1)
