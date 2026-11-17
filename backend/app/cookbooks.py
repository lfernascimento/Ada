"""Catálogo de referências externas, não executáveis."""
from fastapi import APIRouter, HTTPException
from pathlib import Path
import json, hashlib
from .db import connect

router = APIRouter()
CATALOG = [
    ('chief-of-staff', 'Subagentes e delegação', 'Exemplo de delegação de tarefas e definição de especialistas.', 'https://platform.claude.com/cookbook/claude-agent-sdk-01-the-chief-of-staff-agent'),
    ('sdk-migration', 'Agentes programáticos e permissões', 'Exemplo de definições de agentes e configuração de permissões no SDK.', 'https://platform.claude.com/cookbook/claude-agent-sdk-04-migrating-from-openai-agents-sdk'),
    ('dynamic-workflows', 'Fluxos dinâmicos', 'Referência sobre coordenação e verificação; pode consumir muitos tokens.', 'https://platform.claude.com/cookbook/claude-agent-sdk-08-dynamic-workflows'),
]

def prepare_catalog(c):
    c.execute('CREATE TABLE IF NOT EXISTS cookbook_references (slug TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL, source_url TEXT NOT NULL)')
    c.executemany('INSERT INTO cookbook_references(slug,title,summary,source_url) VALUES(?,?,?,?) ON CONFLICT (slug) DO NOTHING', CATALOG)
    c.execute('CREATE TABLE IF NOT EXISTS cookbook_source_files (revision TEXT NOT NULL, source_path TEXT NOT NULL, source_url TEXT NOT NULL, content TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(revision, source_path))')
    folder = Path(__file__).resolve().parents[1] / 'cookbook_sources'
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    revision = manifest['revision']
    rows = []
    for source_path in manifest['files']:
        content = (folder / Path(source_path).name).read_text(encoding='utf-8')
        url = f'https://github.com/anthropics/claude-cookbooks/blob/{revision}/{source_path}'
        rows.append((revision, source_path, url, content, hashlib.sha256(content.encode('utf-8')).hexdigest()))
    c.executemany('INSERT INTO cookbook_source_files(revision,source_path,source_url,content,sha256) VALUES(?,?,?,?,?) ON CONFLICT (revision,source_path) DO NOTHING', rows)


@router.get('/cookbooks')
def list_cookbooks():
    try:
        with connect() as c:
            rows = c.execute('SELECT slug,title,summary,source_url FROM cookbook_references ORDER BY slug').fetchall()
        return [dict(row) for row in rows]
    except Exception:
        raise HTTPException(503, 'Catálogo ainda não preparado no banco de dados.')

@router.get('/cookbooks/source-files')
def cookbook_source_files():
    try:
        with connect() as c:
            rows = c.execute('SELECT revision,source_path,source_url,sha256 FROM cookbook_source_files ORDER BY source_path').fetchall()
        return [dict(row) for row in rows]
    except Exception:
        raise HTTPException(503, 'Arquivos de referência ainda não preparados no banco de dados.')
