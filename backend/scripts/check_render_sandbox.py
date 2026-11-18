"""Explicit paid smoke test. Never runs merely from importing this module."""
import argparse
import io
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # Load local .env without displaying credentials.
from app.tools.render_sandbox import execute_python, RenderWorkspace


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-paid-test',action='store_true',help='Confirma criação de recursos cobrados no Render.')
    args=parser.parse_args()
    if not args.run_paid_test:
        parser.error('Use --run-paid-test somente depois de conferir credenciais e cobrança no Render.')
    print('Executando trecho Python no Render...',flush=True)
    snippet=execute_python('print(2 + 2)')
    assert snippet['saida'].strip()=='4'
    content=b'import unittest\nclass Tests(unittest.TestCase):\n def test_example(self): self.assertEqual(2+2,4)\n'
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode='w:gz') as tar:
        item=tarfile.TarInfo('repo/test_example.py');item.size=len(content);tar.addfile(item,io.BytesIO(content))
    with RenderWorkspace(buf.getvalue()) as w:
        invalid=w.validate([{'path':'app.py','content':'def broken(\n'}])
        valid=w.validate([{'path':'app.py','content':'value=4\n'}])
    print(json.dumps({'snippet':snippet,'invalid':invalid,'valid':valid},ensure_ascii=False,indent=2))
    assert invalid['status']=='failed' and valid['status']=='passed'
    print('Trecho e validações concluídos; encerramento das três sandboxes confirmado.')


if __name__=='__main__':
    try:main()
    except (RuntimeError,AssertionError):raise SystemExit('Validação incompleta. Confira credenciais, SDK e disponibilidade no painel; não compartilhe segredos.')
