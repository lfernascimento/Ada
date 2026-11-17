"""Verificação local da sandbox sem modelo, rede ou repositório externo."""
import io, json, sys, tarfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.tools.workspace_sandbox import validate_workspace

content=b'import unittest\n\nclass Tests(unittest.TestCase):\n    def test_example(self):\n        self.assertEqual(2 + 2, 4)\n'
buffer=io.BytesIO()
with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
    item=tarfile.TarInfo('repository/test_example.py');item.size=len(content)
    archive.addfile(item,io.BytesIO(content))
data=buffer.getvalue()
valid=validate_workspace([{'path':'app.py','content':'value = 1\n'}],data)
invalid=validate_workspace([{'path':'app.py','content':'def broken(\n'}],data)
print(json.dumps({'valido':valid,'invalido':invalid},ensure_ascii=False,indent=2))
if valid.get('status')!='passed' or invalid.get('status')!='failed':
    raise SystemExit('A verificação não passou. Confira o resultado acima.')
print('Sandbox validada com teste passando e erro de sintaxe detectado.')
