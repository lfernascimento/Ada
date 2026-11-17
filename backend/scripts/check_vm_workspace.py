"""Teste de uma única VM, sem chave de modelo ou repositório externo."""
import io,json,os,sys,tarfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.tools import workspace_sandbox
from app.tools.workspace_sandbox import validate_workspace

_original_run=workspace_sandbox.subprocess.run
def diagnostic_run(command, **kwargs):
    stage=' '.join(Path(str(x)).name if i==0 else str(x) for i,x in enumerate(command[:4]))
    print('Etapa:',stage,flush=True)
    try:
        result=_original_run(command,**kwargs)
    except Exception as exc:
        print('Falha:',type(exc).__name__,str(exc)[:500],flush=True)
        raise
    print('Código:',result.returncode,flush=True)
    if result.returncode:
        for label in ('stderr','stdout'):
            value=getattr(result,label,None)
            if value:
                print(label+':',value.decode('utf-8',errors='replace')[:1500] if isinstance(value,bytes) else str(value)[:1500],flush=True)
    return result
workspace_sandbox.subprocess.run=diagnostic_run
os.environ['ADA_SANDBOX_BACKEND']='multipass'
content=b'import unittest\nclass Tests(unittest.TestCase):\n    def test_example(self):\n        self.assertEqual(2 + 2, 4)\n'
buffer=io.BytesIO()
with tarfile.open(fileobj=buffer,mode='w:gz') as tar:
    item=tarfile.TarInfo('repo/test_example.py');item.size=len(content);tar.addfile(item,io.BytesIO(content))
result=validate_workspace([{'path':'app.py','content':'value = 1\n'}],buffer.getvalue())
print(json.dumps(result,ensure_ascii=False,indent=2))
if result.get('status')!='passed' or not any(x.get('command','').startswith('python3 -m unittest') and x.get('status')=='passed' for x in result.get('checks',[])):
    raise SystemExit('Não foi possível validar a VM; confira o resultado acima.')
print('VM validada: teste executado e limpeza solicitada.')
