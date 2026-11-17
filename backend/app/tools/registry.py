"""Low-level tool definitions. External content is always untrusted data."""
from __future__ import annotations
import http.client, ipaddress, json, re, socket, ssl, unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from pydantic import BaseModel, ConfigDict, Field
from ..db import connect
from ..config import ROOT
from .sandbox import PythonSandboxArgs

class ToolError(Exception): pass
class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')
class ReadURL(Strict):
    url: str = Field(min_length=1, max_length=2048)
class SaveReport(Strict):
    titulo: str = Field(min_length=1, max_length=120)
    markdown: str = Field(min_length=1, max_length=100000)
class SearchDocuments(Strict):
    pergunta: str = Field(min_length=1, max_length=2000)

class TextExtractor(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.title=[]; self.hidden=0; self.in_title=False
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript','svg'): self.hidden+=1
        if tag=='title': self.in_title=True
        if tag in ('p','br','h1','h2','h3','li','article','section'): self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript','svg') and self.hidden: self.hidden-=1
        if tag=='title': self.in_title=False
    def handle_data(self,data):
        if self.in_title: self.title.append(data)
        if not self.hidden: self.parts.append(data+' ')

def _validated_addresses(host,port):
    try: addresses={item[4][0] for item in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)}
    except socket.gaierror as e: raise ToolError('Não foi possível resolver o endereço.') from e
    if not addresses or any(not ipaddress.ip_address(ip.split('%')[0]).is_global for ip in addresses):
        raise ToolError('Endereço local, privado ou reservado bloqueado.')
    return addresses

class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self,host,port,ip): super().__init__(host,port,timeout=10); self.ip=ip
    def connect(self): self.sock=socket.create_connection((self.ip,self.port),self.timeout)
class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,port,ip): super().__init__(host,port,timeout=10,context=ssl.create_default_context()); self.ip=ip
    def connect(self):
        raw=socket.create_connection((self.ip,self.port),self.timeout)
        self.sock=self._context.wrap_socket(raw,server_hostname=self.host)

def read_url(args:ReadURL):
    current=args.url
    for redirects in range(4):
        try: parsed=urlsplit(current); port=parsed.port or (443 if parsed.scheme=='https' else 80)
        except ValueError as e: raise ToolError('URL inválida.') from e
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password: raise ToolError('Apenas URLs HTTP(S) públicas são aceitas.')
        if port not in (80,443): raise ToolError('Porta não permitida.')
        addresses=_validated_addresses(parsed.hostname,port)
        ip=sorted(addresses)[0]; cls=PinnedHTTPS if parsed.scheme=='https' else PinnedHTTP
        conn=cls(parsed.hostname,port,ip)
        try:
            path=parsed.path or '/'
            if parsed.query: path+='?'+parsed.query
            conn.request('GET',path,headers={'User-Agent':'Ada/1.0','Accept':'text/html,text/plain;q=0.9','Accept-Encoding':'identity'})
            res=conn.getresponse()
            if res.status in (301,302,303,307,308):
                location=res.getheader('Location')
                if not location or redirects==3: raise ToolError('Limite de redirecionamentos atingido.')
                current=urljoin(current,location); continue
            if res.status<200 or res.status>=300: raise ToolError(f'A página respondeu com HTTP {res.status}.')
            content_type=res.getheader('Content-Type','').lower()
            if not any(t in content_type for t in ('text/html','text/plain','application/xhtml+xml')): raise ToolError('Formato da página não é texto ou HTML.')
            raw=res.read(2_000_001)
            if len(raw)>2_000_000: raise ToolError('Página ultrapassa 2 MB.')
            text=raw.decode('utf-8',errors='replace'); parser=TextExtractor(); parser.feed(text)
            result=re.sub(r'[ \t]+',' ',''.join(parser.parts)); result=re.sub(r'\n\s*\n+', '\n\n',result).strip()
            return {'title':''.join(parser.title).strip()[:300], 'text':result[:6000], 'url':current}
        except (OSError, http.client.HTTPException, ssl.SSLError) as e: raise ToolError('Não foi possível ler a página com segurança.') from e
        finally: conn.close()
    raise ToolError('Limite de redirecionamentos atingido.')

def report_path(title):
    name=unicodedata.normalize('NFKD',title).encode('ascii','ignore').decode().lower()
    slug=re.sub(r'[^a-z0-9]+','-',name).strip('-')[:60] or 'relatorio'
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    return ROOT/'relatorios'/f'{slug}-{stamp}.md'

def save_report(args:SaveReport):
    path=report_path(args.titulo); path.parent.mkdir(exist_ok=True)
    with path.open('x',encoding='utf-8',newline='\n') as f: f.write(args.markdown)
    return {'arquivo':path.name}

def search_documents(args:SearchDocuments,agent_id:int):
    terms=set(re.findall(r'\w+',args.pergunta.lower(),flags=re.UNICODE)); terms={x for x in terms if len(x)>2}
    with connect() as c:
        rows=c.execute('SELECT d.name,c.content,c.position FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.agent_id=?',(agent_id,)).fetchall()
    ranked=[]
    for row in rows:
        words=set(re.findall(r'\w+',row['content'].lower(),flags=re.UNICODE)); score=len(terms & words)/max(1,len(terms))
        if score>=0.2: ranked.append((score,dict(row)))
    ranked.sort(key=lambda x:x[0],reverse=True)
    return {'trechos':[r for _,r in ranked[:4]],'sem_evidencia':not ranked}

REGISTRY={'ler_url':(ReadURL,'low'), 'salvar_relatorio':(SaveReport,'external_effect'), 'consultar_documentos':(SearchDocuments,'low'), 'executar_codigo':(PythonSandboxArgs,'external_effect')}
