"""Guest-only runner. This file must never execute submitted code on the Ada host."""
import base64,json,os,pathlib,subprocess,sys,tempfile

def main():
    payload=json.load(open(sys.argv[1],encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='analysis-') as directory:
        root=pathlib.Path(directory)
        for name,data in payload['files'].items():
            p=root/name
            if p.is_absolute() and not p.resolve().is_relative_to(root):raise ValueError('path')
            if not p.resolve().is_relative_to(root) or any(part.startswith('.') for part in pathlib.PurePosixPath(name).parts):raise ValueError('path')
            p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode(data,validate=True))
        (root/'outputs').mkdir(exist_ok=True)
        program=root/'_task.py';program.write_text(payload['code'],encoding='utf-8')
        log=root/'_run.log'
        with log.open('wb') as output:
            try:
                proc=subprocess.run([sys.executable,'-I','-B',str(program)],cwd=root,stdout=output,stderr=subprocess.STDOUT,timeout=8,env={'PATH':os.environ.get('PATH','')})
                status=proc.returncode
            except subprocess.TimeoutExpired:status=124
        files={};total=0
        if (root/'outputs').is_symlink():raise ValueError('symlink')
        for p in (root/'outputs').rglob('*'):
            if p.is_symlink():raise ValueError('symlink')
            if not p.is_file():continue
            total+=p.stat().st_size
            if total>24000 or len(files)>=4:raise ValueError('output limit')
            files[p.relative_to(root).as_posix()]=base64.b64encode(p.read_bytes()).decode()
        with log.open('rb') as output:out=output.read(8000).decode('utf-8',errors='replace')
        print(json.dumps({'codigo_saida':status,'saida':out,'files':files},ensure_ascii=False))

if __name__=='__main__':main()
