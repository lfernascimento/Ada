"""Verifica duas tentativas na mesma VM sem modelo ou dados externos."""
import io,json,os,sys,tarfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.tools.workspace_sandbox import open_workspace
os.environ['ADA_SANDBOX_BACKEND']='multipass'
content=b'import unittest\nclass Tests(unittest.TestCase):\n    def test_example(self):\n        self.assertEqual(2+2,4)\n'
buffer=io.BytesIO()
with tarfile.open(fileobj=buffer,mode='w:gz') as tar:
    item=tarfile.TarInfo('repo/test_example.py');item.size=len(content);tar.addfile(item,io.BytesIO(content))
print('Criando VM; a primeira inicialização pode levar alguns minutos...',flush=True)
with open_workspace(buffer.getvalue()) as workspace:
    invalid=workspace.validate([{'path':'app.py','content':'def broken(\n'}])
    print('Primeira tentativa:',invalid.get('status'),flush=True)
    valid=workspace.validate([{'path':'app.py','content':'value = 1\n'}])
    print('Segunda tentativa:',valid.get('status'),flush=True)
print(json.dumps({'primeira':invalid,'segunda':valid},ensure_ascii=False,indent=2))
if invalid.get('status')!='failed' or valid.get('status')!='passed':raise SystemExit('Validação incompleta; confira as saídas acima.')
print('Duas verificações na mesma VM concluídas; limpeza solicitada.')
