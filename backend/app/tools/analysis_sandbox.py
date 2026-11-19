"""Transfer explicitly selected workspace files to an existing isolated executor."""
import base64,json,os,tempfile
from pathlib import Path
from .sandbox import PythonSandboxArgs,SandboxError

RUNNER=Path(__file__).resolve().parents[3]/'sandbox/analysis_runner.py'

def execute(code,files):
    payload=json.dumps({'code':code,'files':files},ensure_ascii=False).encode()
    if len(payload)>1_500_000:raise SandboxError('Os arquivos selecionados excedem o limite desta execução.')
    if os.getenv('ADA_SANDBOX_BACKEND','docker').lower()=='render':
        from .render_sandbox import _execute
        with tempfile.TemporaryDirectory(prefix='ada-analysis-') as directory:
            source=Path(directory)/'input.json';source.write_bytes(payload)
            status,output,_=_execute('python3 -I -B /tmp/ada-analysis.py /tmp/ada-analysis.json',[(RUNNER,'/tmp/ada-analysis.py'),(source,'/tmp/ada-analysis.json')])
        if status:raise SandboxError('A análise não retornou um resultado válido na sandbox.')
    else:
        # Only this loader runs in Docker. No workspace or host directory is mounted.
        from .sandbox import execute_python
        loader="import pathlib,base64,sys; p=pathlib.Path('/tmp/input.json');p.write_bytes(base64.b64decode("+repr(base64.b64encode(payload).decode())+"));sys.argv=['analysis',str(p)];exec(compile("+repr(RUNNER.read_text(encoding='utf-8'))+",'<runner>','exec'))"
        result=execute_python(PythonSandboxArgs.model_construct(codigo=loader))
        output=result['saida']
    try:
        result=json.loads(output)
        if not isinstance(result.get('codigo_saida'),int) or not isinstance(result.get('saida'),str) or len(result['saida'])>8000:raise ValueError()
        captured=result.get('files',{})
        if not isinstance(captured,dict) or len(captured)>4 or sum(len(base64.b64decode(v,validate=True)) for v in captured.values())>24000:raise ValueError()
        from ..sdk_workspace import safe_name
        for name in captured:
            if not safe_name(name).startswith('outputs/'):raise ValueError()
        return result
    except (ValueError,TypeError,KeyError):raise SandboxError('O executor retornou um resultado inválido.') from None
