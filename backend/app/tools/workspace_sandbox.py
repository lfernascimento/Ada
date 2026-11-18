"""Execution backends for disposable local development workspaces."""
import base64,json,os,shutil,subprocess,tempfile,threading,uuid
from pathlib import Path

MAX_ARCHIVE=8*1024*1024
ROOT=Path(__file__).resolve().parents[3]
_vm_slot=threading.BoundedSemaphore(1)


def _result(proc, failure):
    if proc.returncode:
        return {'status':'unavailable','message':failure,'checks':[]}
    data=json.loads(proc.stdout.decode('utf-8'))
    if data.get('status') not in ('passed','failed','not_applicable','unavailable'):raise ValueError()
    return data


def _payload(files,archive):
    return json.dumps({'archive':base64.b64encode(archive).decode('ascii'),'files':[{'path':x['path'],'content':x['content']} for x in files]},ensure_ascii=False).encode('utf-8')


def _docker(files,archive):
    binary=shutil.which('docker')
    if not binary:return {'status':'unavailable','message':'Docker não encontrado; testes não executados.','checks':[]}
    name='ada-workspace-'+uuid.uuid4().hex
    cmd=[binary,'run','--name',name,'--rm','--pull=never','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=96','--memory=512m','--cpus=1','--user=65534:65534','--tmpfs=/tmp:rw,nosuid,nodev,size=128m','-i','ada-workspace:local']
    try:
        proc=subprocess.run(cmd,input=_payload(files,archive),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=110,check=False)
        return _result(proc,'Sandbox indisponível; confira se a imagem ada-workspace:local foi construída.')
    finally:
        try:subprocess.run([binary,'rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5,check=False)
        except (OSError,subprocess.SubprocessError):pass


class MultipassWorkspace:
    """One disposable VM per task; each check materializes a clean repository snapshot."""
    def __init__(self,archive):
        self.archive=archive; self.binary=None; self.name='ada-task-'+uuid.uuid4().hex[:20]; self.acquired=False; self.warnings=[]
    def __enter__(self):
        self.binary=shutil.which('multipass')
        if not self.binary and os.name=='nt':
            candidate=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Multipass'/'bin'/'multipass.exe'
            if candidate.is_file():self.binary=str(candidate)
        if not self.binary:raise RuntimeError('Multipass não encontrado; instale e configure o executor local.')
        if not _vm_slot.acquire(blocking=False):raise RuntimeError('Outra tarefa está usando a VM local; tente novamente.')
        self.acquired=True
        try:
            launch=[self.binary,'launch','24.04','--name',self.name,'--cpus','2','--memory','2G','--disk','8G','--timeout','600','--cloud-init',str(ROOT/'sandbox'/'cloud-init.yaml')]
            p=subprocess.run(launch,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=620,check=False)
            if p.returncode:raise RuntimeError('Falha ao iniciar a VM. Confira Multipass, virtualização e internet.')
            for args in (['cloud-init','status','--wait'],['python3','--version'],['node','--version'],['npm','--version'],['sudo','iptables','-C','OUTPUT','-m','owner','--uid-owner','ada-sandbox','-j','REJECT'],['sudo','ip6tables','-C','OUTPUT','-m','owner','--uid-owner','ada-sandbox','-j','REJECT']):
                p=subprocess.run([self.binary,'exec',self.name,'--',*args],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=620,check=False)
                if args[0]=='cloud-init' and p.returncode==2 and b'status: done' in (getattr(p,'stdout',b'') or b''):
                    self.warnings.append('O cloud-init concluiu com avisos; ferramentas e regras de rede foram verificadas separadamente.')
                    continue
                if p.returncode:
                    output=(getattr(p,'stdout',b'') or b'')+(getattr(p,'stderr',b'') or b'')
                    details=output.decode('utf-8',errors='replace')[:1200] if isinstance(output,bytes) else str(output)[:1200]
                    raise RuntimeError('Falha na etapa '+args[0]+': '+details)

            return self
        except BaseException:
            self.close();raise
    def kill_workers(self):
        p=subprocess.run([self.binary,'exec',self.name,'--','sudo','pkill','-KILL','-u','ada-sandbox'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
        if p.returncode not in (0,1):raise RuntimeError('Não foi possível encerrar processos da tentativa anterior.')
    def validate(self,files):
        self.kill_workers()
        payload_path=None
        try:
            with tempfile.NamedTemporaryFile(prefix='ada-vm-',suffix='.json',delete=False) as f:
                payload_path=Path(f.name);f.write(_payload(files,self.archive))
            guest_file='/tmp/ada-input-'+uuid.uuid4().hex+'.json'
            p=subprocess.run([self.binary,'transfer',str(payload_path),self.name+':'+guest_file],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,check=False)
            if p.returncode:raise RuntimeError('Falha ao transferir arquivos de trabalho para a VM.')
            p=subprocess.run([self.binary,'exec',self.name,'--','sudo','chmod','644',guest_file],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
            if p.returncode:raise RuntimeError('Falha ao preparar os arquivos de trabalho na VM.')
            source=(ROOT/'sandbox'/'runner.py').read_bytes()
            code="import base64;exec(compile(base64.b64decode('"+base64.b64encode(source).decode('ascii')+"'),'<ada-runner>','exec'))"
            p=subprocess.run([self.binary,'exec',self.name,'--','sudo','-u','ada-sandbox','--','python3','-I','-c',code,guest_file],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=110,check=False)
            result=_result(p,'A execução na VM não foi concluída.')
            if self.warnings:result['warnings']=list(self.warnings)
            return result
        finally:
            if payload_path is not None:
                try:payload_path.unlink(missing_ok=True)
                except OSError:pass
            self.kill_workers()
    def close(self):
        if self.acquired:
            try:
                p=subprocess.run([self.binary,'delete','--purge',self.name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,check=False)
                if p.returncode:raise RuntimeError('Limpeza da VM não confirmada: '+self.name+'. Verifique multipass list antes de continuar.')
            except (OSError,subprocess.SubprocessError) as exc:
                raise RuntimeError('Limpeza da VM não confirmada: '+self.name) from exc
            else:self.acquired=False;_vm_slot.release()
    def __exit__(self,*args):self.close()

class DockerWorkspace:
    def __init__(self,archive):self.archive=archive
    def __enter__(self):return self
    def validate(self,files):return _docker(files,self.archive)
    def __exit__(self,*args):pass

def open_workspace(archive):
    if not archive:raise RuntimeError('Cópia do repositório indisponível; testes não executados.')
    backend=os.getenv('ADA_SANDBOX_BACKEND','docker').strip().lower()
    if backend=='multipass':return MultipassWorkspace(archive)
    if backend=='docker':return DockerWorkspace(archive)
    raise RuntimeError('Executor de sandbox desconhecido.')

def _multipass(files,archive):
    with MultipassWorkspace(archive) as workspace:return workspace.validate(files)

def validate_workspace(files,archive=None):
    if not archive:return {'status':'unavailable','message':'Cópia do repositório indisponível; testes não executados.','checks':[]}
    try:
        backend=os.getenv('ADA_SANDBOX_BACKEND','docker').strip().lower()
        if backend=='docker':return _docker(files,archive)
        if backend=='multipass':return _multipass(files,archive)
        return {'status':'unavailable','message':'Executor de sandbox desconhecido.','checks':[]}
    except RuntimeError as exc:
        return {'status':'unavailable','message':str(exc),'checks':[]}
    except (OSError,subprocess.SubprocessError,ValueError,KeyError):
        return {'status':'unavailable','message':'Não foi possível executar a sandbox; testes não executados.','checks':[]}
