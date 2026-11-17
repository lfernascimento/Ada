"""Run untrusted project tests inside the restricted container. No credentials or network."""
import base64, io, json, os, pathlib, resource, subprocess, sys, tarfile, tempfile

ROOT = pathlib.Path('/tmp/workspace')
MAX_FILES = 5000
MAX_BYTES = 30 * 1024 * 1024

def safe_path(name):
    path = pathlib.PurePosixPath(name)
    if not name or path.is_absolute() or any(part in ('', '.', '..', '.git') for part in name.split('/')):
        raise ValueError('Caminho inválido no repositório.')
    return path

def unpack(data):
    total = count = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for item in archive:
            parts = pathlib.PurePosixPath(item.name).parts
            if len(parts) < 2: continue
            relative = '/'.join(parts[1:])
            safe_path(relative)
            if item.isdir(): continue
            if not item.isfile(): continue
            count += 1; total += item.size
            if count > MAX_FILES or total > MAX_BYTES: raise ValueError('Repositório excede os limites da sandbox.')
            dest = ROOT.joinpath(*pathlib.PurePosixPath(relative).parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(item) as src, dest.open('wb') as out:
                while chunk := src.read(65536): out.write(chunk)

def run(command):
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
    with open('/tmp/test-output', 'wb') as output:
        try:
            process = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, timeout=45, preexec_fn=limits,
                env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp','TMPDIR':'/tmp','CI':'1','PYTHONDONTWRITEBYTECODE':'1','npm_config_cache':'/tmp/npm-cache'})
            status = 'passed' if process.returncode == 0 else 'failed'
        except subprocess.TimeoutExpired:
            status = 'failed'
    text = pathlib.Path('/tmp/test-output').read_bytes()[:16000].decode('utf-8', errors='replace')
    return {'command':' '.join(command), 'status':status, 'output':text}

def main():
    if len(sys.argv)>1:
        with open(sys.argv[1],encoding='utf-8') as stream: payload=json.load(stream)
    else:
        payload=json.load(sys.stdin)
    global ROOT
    ROOT=pathlib.Path(tempfile.mkdtemp(prefix='ada-workspace-',dir='/tmp'))
    unpack(base64.b64decode(payload['archive'], validate=True))
    for item in payload['files']:
        path = safe_path(item['path']); dest = ROOT.joinpath(*path.parts)
        if not dest.parent.is_dir(): dest.parent.mkdir(parents=True)
        dest.write_text(item['content'], encoding='utf-8')
    checks = []
    py_files = [x for x in payload['files'] if x['path'].endswith('.py')]
    syntax_errors = []
    for item in py_files:
        try: compile(item['content'], item['path'], 'exec')
        except SyntaxError as exc: syntax_errors.append(f"{item['path']}:{exc.lineno}: {exc.msg}")
    if py_files:
        checks.append({'command':'Sintaxe Python', 'status':'failed' if syntax_errors else 'passed', 'output':'\n'.join(syntax_errors)})
    package = ROOT / 'package.json'
    if package.is_file():
        try: scripts = json.loads(package.read_text(encoding='utf-8')).get('scripts', {})
        except (ValueError, UnicodeError): scripts = {}
        if isinstance(scripts,dict) and scripts.get('test'): checks.append(run(['npm','test']))
    if any(ROOT.rglob('test_*.py')):
        check=run(['python3','-m','unittest','discover','-v'])
        if check['status']=='passed' and 'Ran 0 tests' in check['output']:
            check['status']='not_applicable'
        checks.append(check)
    failed = any(x['status']=='failed' for x in checks)
    status = 'failed' if failed else ('passed' if any(x['status']=='passed' for x in checks) else 'not_applicable')
    msg = {'failed':'Ao menos uma verificação falhou na sandbox.', 'passed':'As verificações disponíveis passaram na sandbox.', 'not_applicable':'Nenhuma verificação compatível foi encontrada.'}[status]
    print(json.dumps({'status':status,'message':msg,'checks':checks},ensure_ascii=True))

if __name__=='__main__':
    try: main()
    except Exception:
        print(json.dumps({'status':'unavailable','message':'Não foi possível preparar o repositório na sandbox.','checks':[]}))
