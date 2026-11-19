"""Scoped SDK workspace: durable user files, trusted skills, no host shell."""
import base64,hashlib,json,shutil,tempfile
from contextlib import contextmanager
from pathlib import Path,PurePosixPath
from contextvars import ContextVar
from fastapi import APIRouter,HTTPException
from fastapi.responses import Response
from .db import connect
from .sdk_sessions import ConversationStore

router=APIRouter()
current=ContextVar('sdk_workspace',default=None)
attachments=ContextVar('sdk_runtime_attachments',default=[])
SKILLS=Path(__file__).with_name('sdk_skills')
MAX_FILES=100;MAX_FILE=512_000;MAX_BYTES=8_000_000

def safe_name(value):
    if not isinstance(value,str) or '\\' in value or any(ord(ch)<32 for ch in value):raise ValueError('Caminho inválido.')
    parts=PurePosixPath(value).parts
    if not parts or value.startswith('/') or ':' in value or any(p in ('.','..') or p.startswith('.') for p in value.split('/')):
        raise ValueError('Caminho inválido.')
    return '/'.join(parts)

def owns(c,aid,cid):
    if not c.execute('SELECT 1 FROM conversations WHERE id=? AND agent_id=?',(cid,aid)).fetchone():raise HTTPException(404,'Conversa inexistente.')

class Workspace:
    def __init__(self,aid,cid,spec):
        self.aid=aid;self.cid=cid;self.spec=spec
        self.root=Path(tempfile.gettempdir()).resolve()/'ada-sdk-runtime-v1'
        self.path=self.root/str(aid)/str(cid);self.cleanup_ready=False
        self.store=ConversationStore(aid,cid);self.resume=None;self.files={};self.cost=0.0
        from . import config
        self.fingerprint=hashlib.sha256((str(self.path)+spec.model_dump_json()+config.MODEL_NAME+config.LIGHT_MODEL+'skills-v1').encode()).hexdigest()

    def __enter__(self):
        try:return self._prepare()
        except BaseException:
            if hasattr(self,'token'):current.reset(self.token)
            self.cleanup()
            raise

    def check_directory(self):
        for p in (self.root,self.path.parent,self.path):
            if p.is_symlink() or getattr(p,'is_junction',lambda:False)():raise RuntimeError('Diretório de trabalho inválido.')
        if not self.path.resolve().is_relative_to(self.root):raise RuntimeError('Diretório de trabalho inválido.')

    def cleanup(self):
        if not self.cleanup_ready:return
        self.check_directory()
        shutil.rmtree(self.path,ignore_errors=True)

    def _prepare(self):
        # Never follow a symlink left in a deterministic runtime directory.
        self.check_directory()
        self.path.mkdir(parents=True,exist_ok=True)
        self.cleanup_ready=True
        for child in self.path.iterdir():
            if child.is_dir() and not child.is_symlink():shutil.rmtree(child)
            else:child.unlink()
        with connect() as c:
            owns(c,self.aid,self.cid)
            row=c.execute('SELECT * FROM sdk_runtime_state WHERE conversation_id=?',(self.cid,)).fetchone()
            if row:
                self.files=json.loads(row['files_json'])
                if row['fingerprint']==self.fingerprint and row['session_id']:
                    exists=c.execute("SELECT 1 FROM sdk_session_entries WHERE conversation_id=? AND session_id=? AND subpath='' LIMIT 1",(self.cid,row['session_id'])).fetchone()
                    if exists:self.resume=row['session_id'];self.cost=float(row['sdk_cost_usd'])
            rows=c.execute('SELECT * FROM chat_attachments WHERE conversation_id=? AND message_id IS NOT NULL ORDER BY id DESC LIMIT 12',(self.cid,)).fetchall()
        total=0
        for name,data in self.files.items():
            raw=base64.b64decode(data,validate=True);total+=len(raw)
            if name.startswith('inputs/') or len(raw)>MAX_FILE or total>MAX_BYTES or len(self.files)>MAX_FILES:raise RuntimeError('Arquivos persistidos excedem os limites.')
            target=self.path/safe_name(name);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        input_total=0
        for item in [dict(r) for r in rows]+attachments.get():
            name=Path(item['name'].replace('\\','/')).name
            target=self.path/'inputs'/(str(item['id'])+'-'+name)
            target.parent.mkdir(exist_ok=True)
            raw=base64.b64decode(item['data'],validate=True) if item['data'] else item['extracted'].encode('utf-8')
            input_total+=len(raw)
            if input_total>24_000_000:raise RuntimeError('Os anexos excedem o limite da área de trabalho.')
            if item.get('mime')=='application/pdf':target=target.with_suffix(target.suffix+'.txt')
            target.write_bytes(raw)
        shutil.copytree(SKILLS,self.path/'.claude/skills',dirs_exist_ok=True)
        self.token=current.set(self)
        return self

    def allowed(self,name,args):
        try:
            if name=='Skill':return args.get('skill') in {p.name for p in SKILLS.iterdir() if p.is_dir()}
            if name=='TodoWrite':return len(json.dumps(args))<=12000
            if name not in ('Read','Write','Edit','Glob','Grep'):
                return name in ('Agent','Task','WebSearch') or name.startswith('mcp__ada__')
            value=args.get('file_path') if name in ('Read','Write','Edit') else args.get('path','.')
            if not isinstance(value,str):return False
            candidate=Path(value)
            if not candidate.is_absolute():candidate=self.path/candidate
            resolved=candidate.resolve();rel=resolved.relative_to(self.path.resolve())
            if any(p.startswith('.') for p in rel.parts):
                return name=='Read' and resolved.is_relative_to((self.path/'.claude/skills').resolve())
            if name in ('Write','Edit'):
                if rel.parts and rel.parts[0]=='inputs':return False
                if len(json.dumps(args).encode())>MAX_FILE:return False
            if name in ('Glob','Grep'):
                pattern=args.get('pattern' if name=='Glob' else 'glob','')
                if not isinstance(pattern,str) or pattern.startswith(('/','\\')) or '..' in pattern or ':' in pattern or '\\' in pattern:return False
            return True
        except (ValueError,OSError):return False

    def capture(self):
        files={};total=0
        for p in self.path.rglob('*'):
            rel=p.relative_to(self.path)
            if any(part.startswith('.') for part in rel.parts) or rel.parts[0]=='inputs':continue
            if p.is_symlink():raise RuntimeError('Links simbólicos não são permitidos na área de trabalho.')
            if not p.is_file():continue
            size=p.stat().st_size;total+=size
            if size>MAX_FILE or total>MAX_BYTES or len(files)>=MAX_FILES:raise RuntimeError('A área de trabalho excedeu o limite de arquivos.')
            files[rel.as_posix()]=base64.b64encode(p.read_bytes()).decode('ascii')
        return files

    def save(self,sid=None,cost=None):
        if self.store.failed:raise RuntimeError('A persistência da sessão falhou; tarefa não confirmada.')
        files=self.capture()
        with connect() as c:
            owns(c,self.aid,self.cid)
            # Delivery can persist files mid-turn; only a completed SDK result advances resume.
            row=c.execute('SELECT session_id,sdk_cost_usd FROM sdk_runtime_state WHERE conversation_id=?',(self.cid,)).fetchone()
            sid=sid if sid is not None else (row['session_id'] if row else '')
            cost=cost if cost is not None else (row['sdk_cost_usd'] if row else 0)
            c.execute('INSERT INTO sdk_runtime_state(conversation_id,fingerprint,session_id,files_json,sdk_cost_usd) VALUES(?,?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET fingerprint=excluded.fingerprint,session_id=excluded.session_id,files_json=excluded.files_json,sdk_cost_usd=excluded.sdk_cost_usd',(self.cid,self.fingerprint,sid,json.dumps(files),cost))
            if sid:c.execute('DELETE FROM sdk_session_entries WHERE conversation_id=? AND session_id<>?',(self.cid,sid))

    def __exit__(self,*args):
        current.reset(self.token)
        self.cleanup()

def invalidate(cid):
    """Never resume the uncommitted user turn after a failure or cancellation."""
    with connect() as c:
        c.execute("UPDATE sdk_runtime_state SET session_id='',sdk_cost_usd=0 WHERE conversation_id=?",(cid,))
        c.execute('DELETE FROM sdk_session_entries WHERE conversation_id=?',(cid,))

@router.get('/agents/{aid}/conversations/{cid}/workspace/files')
def list_files(aid:int,cid:int):
    with connect() as c:
        owns(c,aid,cid);row=c.execute('SELECT files_json FROM sdk_runtime_state WHERE conversation_id=?',(cid,)).fetchone()
    files=json.loads(row['files_json']) if row else {}
    return [{'path':name,'size':len(base64.b64decode(data))} for name,data in files.items()]

@router.get('/agents/{aid}/conversations/{cid}/workspace/file')
def download(aid:int,cid:int,path:str):
    try:path=safe_name(path)
    except ValueError:raise HTTPException(400,'Caminho inválido.')
    with connect() as c:
        owns(c,aid,cid);row=c.execute('SELECT files_json FROM sdk_runtime_state WHERE conversation_id=?',(cid,)).fetchone()
    data=json.loads(row['files_json']).get(path) if row else None
    if data is None:raise HTTPException(404,'Arquivo não encontrado.')
    from urllib.parse import quote
    return Response(base64.b64decode(data),media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(PurePosixPath(path).name)})
