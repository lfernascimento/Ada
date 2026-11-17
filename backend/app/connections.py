"""Local, read-only service connections. Tokens remain in server memory only."""
import base64, hashlib, os, re, secrets, time
from urllib.parse import urlencode, quote
import httpx
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from .config import ROOT

async def local_only(request:Request):
    host=request.url.hostname
    if host not in ('localhost','127.0.0.1','::1'): raise HTTPException(403,'Acesso local necessário.')
    origin=request.headers.get('origin')
    if origin and not re.fullmatch(r'http://(?:localhost|127\.0\.0\.1):(?:517[3-9])',origin): raise HTTPException(403,'Origem não autorizada.')
router=APIRouter(prefix='/connections',dependencies=[Depends(local_only)])
PROVIDERS={
'github':{'label':'GitHub','auth':'https://github.com/login/oauth/authorize','token':'https://github.com/login/oauth/access_token','scope':'read:user','id':'GITHUB_CLIENT_ID','secret':'GITHUB_CLIENT_SECRET'},
'figma':{'label':'Figma','auth':'https://www.figma.com/oauth','token':'https://api.figma.com/v1/oauth/token','scope':'current_user:read file_content:read','id':'FIGMA_CLIENT_ID','secret':'FIGMA_CLIENT_SECRET'},
'drive':{'label':'Google Drive','auth':'https://accounts.google.com/o/oauth2/v2/auth','token':'https://oauth2.googleapis.com/token','scope':'https://www.googleapis.com/auth/drive.file','id':'GOOGLE_CLIENT_ID','secret':'GOOGLE_CLIENT_SECRET'},
}
GOOGLE_SCOPES={
 'docs':('Google Docs','https://www.googleapis.com/auth/documents.readonly'),
 'sheets':('Google Sheets','https://www.googleapis.com/auth/spreadsheets.readonly'),
 'gmail':('Gmail','https://www.googleapis.com/auth/gmail.metadata'),
 'calendar':('Google Calendar','https://www.googleapis.com/auth/calendar.events.readonly'),
 'classroom':('Google Classroom','https://www.googleapis.com/auth/classroom.courses.readonly'),
}
for key,(label,scope) in GOOGLE_SCOPES.items():
    PROVIDERS[key]={'label':label,'auth':'https://accounts.google.com/o/oauth2/v2/auth','token':'https://oauth2.googleapis.com/token','scope':scope,'id':'GOOGLE_CLIENT_ID','secret':'GOOGLE_CLIENT_SECRET'}
GOOGLE_PROVIDERS={'drive', *GOOGLE_SCOPES}
GOOGLE_WRITE_SCOPES={
 'drive':'https://www.googleapis.com/auth/drive.file',
 'docs':'https://www.googleapis.com/auth/drive.file',
 'sheets':'https://www.googleapis.com/auth/drive.file',
 'gmail':'https://www.googleapis.com/auth/gmail.compose',
 'calendar':'https://www.googleapis.com/auth/calendar.events',
 'classroom':'https://www.googleapis.com/auth/classroom.courses',
}

# In-memory intentionally: no plaintext tokens in SQLite or logs. Reconnect after restart.
_pending={}; _tokens={}
def get_provider(provider):
    if provider not in PROVIDERS: raise HTTPException(404,'Integração desconhecida.')
    return PROVIDERS[provider]
def settings(provider):
    p=get_provider(provider); client=os.getenv(p['id'],'').strip(); secret=os.getenv(p['secret'],'').strip()
    return p,client,secret

def callback_url(provider):
    base=os.getenv('ADA_OAUTH_CALLBACK_BASE','http://127.0.0.1:8000').rstrip('/')
    # Public callbacks require a deliberate HTTPS deployment; local default is loopback.
    if base!='http://127.0.0.1:8000' and not base.startswith('https://'): raise HTTPException(503,'URL de retorno OAuth precisa usar HTTPS.')
    return base+'/connections/'+('drive' if provider in GOOGLE_PROVIDERS else provider)+'/callback'

@router.get('')
def list_connections():
    items=[]
    for k,p in PROVIDERS.items():
        _,client,secret=settings(k); ready=bool(client and secret)
        token=_tokens.get(k); connected=bool(token and (not token.get('expires_at') or token['expires_at']>time.time()))
        items.append({'id':k,'name':p['label'],'configured':ready,'connected':connected,'permissions':p['scope'],'access':'leitura','storage':'memória temporária','granted_scopes':token.get('scopes','') if token and connected else ''})
    items.extend([{'id':'mcp','name':'MCP','configured':True,'connected':False,'access':'interno'}, {'id':'sandbox','name':'Sandbox de código','configured':True,'connected':False,'access':'execução manual'}, {'id':'vscode','name':'VS Code','configured':False,'connected':False,'access':'não implementado'}])
    return items

class StartBody(BaseModel):
    frontend_origin: str | None = Field(default=None, max_length=100)
    github_public_repo_write: bool = False
    google_write: bool = False

@router.post('/{provider}/start')
def start(provider:str, request:Request, body:StartBody=StartBody()):
    p,client,secret=settings(provider)
    if not client or not secret: raise HTTPException(503,'Cadastre o aplicativo OAuth e configure o ID e segredo deste serviço no .env.')
    origin=body.frontend_origin
    if origin and (not re.fullmatch(r'http://(?:localhost|127\.0\.0\.1):517[3-9]',origin) or (request.headers.get('origin') and request.headers['origin']!=origin)):
        raise HTTPException(400,'Endereço de retorno do frontend inválido.')
    for key,value in list(_pending.items()):
        if value['expires']<time.time(): _pending.pop(key,None)
    state=secrets.token_urlsafe(32); verifier=secrets.token_urlsafe(64)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    _pending[state]={'provider':provider,'verifier':verifier,'expires':time.time()+600,'frontend_origin':origin}
    scope='read:user public_repo' if provider=='github' and body.github_public_repo_write else p['scope']
    if provider in GOOGLE_PROVIDERS and body.google_write:
        scope=' '.join(dict.fromkeys([p['scope'],GOOGLE_WRITE_SCOPES[provider]]))
    params={'client_id':client,'redirect_uri':callback_url(provider),'response_type':'code','scope':scope,'state':state}
    if provider=='figma' or provider in GOOGLE_PROVIDERS: params.update(code_challenge=challenge,code_challenge_method='S256')
    if provider in GOOGLE_PROVIDERS: params.update(access_type='online',include_granted_scopes='false')
    return {'url':p['auth']+'?'+urlencode(params),'expires_in':600}

@router.get('/{provider}/callback',response_class=HTMLResponse)
async def callback(provider:str,state:str='',code:str='',error:str=''):
    item=_pending.pop(state,None)
    if item and provider=='drive' and item['provider'] in GOOGLE_PROVIDERS: provider=item['provider']
    p,client,secret=settings(provider)
    if not item or item['provider']!=provider or item['expires']<time.time(): raise HTTPException(400,'Autorização inválida ou expirada.')
    if error:
        if item.get('frontend_origin'):
            return RedirectResponse(item['frontend_origin']+'/?connection='+provider+'&status=cancelled',status_code=303,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        return HTMLResponse('<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>Ada</title><p>A autorização foi cancelada. Você pode fechar esta aba.</p></html>',status_code=400)
    if not code: raise HTTPException(400,'Código de autorização ausente.')
    body={'grant_type':'authorization_code','code':code,'redirect_uri':callback_url(provider)}
    headers={'Accept':'application/json'}; auth=None
    if provider=='figma': auth=(client,secret);body['code_verifier']=item['verifier']
    else:
        body.update(client_id=client,client_secret=secret)
        if provider in GOOGLE_PROVIDERS: body['code_verifier']=item['verifier']
    try:
        async with httpx.AsyncClient(timeout=15,trust_env=False) as c:
            response=await c.post(p['token'],data=body,headers=headers,auth=auth);response.raise_for_status();data=response.json()
        token=data.get('access_token')
        if not token: raise ValueError('token ausente')
    except (httpx.HTTPError,ValueError): raise HTTPException(502,'Não foi possível concluir a autorização. Confira a configuração OAuth.')
    _tokens[provider]={'access_token':token,'expires_at':time.time()+int(data.get('expires_in',0)) if data.get('expires_in') else None,'scopes':data.get('scope','')}
    if item.get('frontend_origin'):
        return RedirectResponse(item['frontend_origin']+'/?connection='+provider+'&status=connected',status_code=303,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
    return HTMLResponse('<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>Ada</title><p>Conexão autorizada. Volte à Ada e atualize o estado das conexões. Você pode fechar esta aba.</p></html>',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; frame-ancestors 'none'"})

@router.post('/{provider}/disconnect')
def disconnect(provider:str):
    get_provider(provider);_tokens.pop(provider,None)
    return {'connected':False,'note':'A sessão local foi apagada; para revogar o acesso, use também as configurações do serviço.'}

@router.get('/{provider}/check')
async def check(provider:str):
    get_provider(provider); session=_tokens.get(provider)
    if not session or (session['expires_at'] and session['expires_at']<=time.time()): raise HTTPException(401,'Conecte novamente este serviço.')
    urls={'github':'https://api.github.com/user','figma':'https://api.figma.com/v1/me','drive':'https://www.googleapis.com/drive/v3/about?fields=user(displayName)',
          'gmail':'https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=1',
          'calendar':'https://www.googleapis.com/calendar/v3/calendars/primary/events?maxResults=1',
          'classroom':'https://classroom.googleapis.com/v1/courses?pageSize=1'}
    # Docs and Sheets only expose a document-by-ID operation; authorization itself is checked here.
    if provider in ('docs','sheets'): return {'connected':True,'account':'Serviço autorizado; informe um arquivo para testar.'}
    try:
        async with httpx.AsyncClient(timeout=15,trust_env=False,follow_redirects=False) as c:
            r=await c.get(urls[provider],headers={'Authorization':'Bearer '+session['access_token'],'Accept':'application/json','User-Agent':'Ada-local'});r.raise_for_status(); data=r.json()
    except (httpx.HTTPError,ValueError): raise HTTPException(502,'Não foi possível verificar a conexão com o serviço.')
    if provider=='github': label=data.get('login','Conta conectada')
    elif provider=='figma': label=data.get('handle','Conta conectada')
    elif provider=='drive': label=(data.get('user') or {}).get('displayName','Conta conectada')
    else: label='Serviço autorizado'
    return {'connected':True,'account':str(label)[:100]}

@router.get('/github/repositories')
async def public_repositories(user:str):
    if not re.fullmatch(r'[A-Za-z0-9-]{1,39}',user): raise HTTPException(422,'Usuário do GitHub inválido.')
    try:
        async with httpx.AsyncClient(timeout=15,trust_env=False) as c:
            r=await c.get('https://api.github.com/users/'+user+'/repos',params={'per_page':30,'type':'owner'},headers={'Accept':'application/vnd.github+json','User-Agent':'Ada-local'});r.raise_for_status()
        return [{'name':x['name'],'full_name':x['full_name'],'url':x['html_url'],'private':False} for x in r.json() if not x.get('private')]
    except (httpx.HTTPError,ValueError): raise HTTPException(502,'Não foi possível consultar os repositórios públicos.')

@router.get('/drive/files')
async def drive_files():
    session=_tokens.get('drive')
    if not session or (session['expires_at'] and session['expires_at']<=time.time()): raise HTTPException(401,'Conecte o Google Drive novamente.')
    try:
        async with httpx.AsyncClient(timeout=15,trust_env=False) as c:
            r=await c.get('https://www.googleapis.com/drive/v3/files',params={'pageSize':30,'fields':'files(id,name,mimeType,modifiedTime)','q':'trashed = false'},headers={'Authorization':'Bearer '+session['access_token']});r.raise_for_status()
        return r.json().get('files',[])
    except (httpx.HTTPError,ValueError): raise HTTPException(502,'Não foi possível consultar os arquivos autorizados do Drive.')

@router.get('/figma/files/{file_key}')
async def figma_file(file_key:str):
    if not re.fullmatch(r'[A-Za-z0-9]{1,100}',file_key): raise HTTPException(422,'Identificador de arquivo inválido.')
    session=_tokens.get('figma')
    if not session or (session['expires_at'] and session['expires_at']<=time.time()): raise HTTPException(401,'Conecte o Figma novamente.')
    try:
        async with httpx.AsyncClient(timeout=15,trust_env=False) as c:
            r=await c.get('https://api.figma.com/v1/files/'+file_key,params={'depth':1},headers={'Authorization':'Bearer '+session['access_token']});r.raise_for_status(); data=r.json()
        return {'name':data.get('name'),'lastModified':data.get('lastModified'),'document':data.get('document')}
    except (httpx.HTTPError,ValueError): raise HTTPException(502,'Não foi possível consultar o arquivo do Figma.')


@router.get('/figma/files/{file_key}/render')
async def figma_render(file_key:str,node_id:str):
    if not re.fullmatch(r'[A-Za-z0-9]{1,100}',file_key) or not re.fullmatch(r'[0-9]+[:-][0-9]+',node_id): raise HTTPException(422,'Arquivo ou identificador de camada inválido.')
    item=_tokens.get('figma')
    if not item or (item.get('expires_at') and item['expires_at']<=time.time()): raise HTTPException(401,'Conecte o Figma novamente.')
    node_id=node_id.replace('-',':')
    try:
        async with httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False) as client:
            r=await client.get('https://api.figma.com/v1/images/'+file_key,params={'ids':node_id,'format':'png','scale':1},headers={'Authorization':'Bearer '+item['access_token']});r.raise_for_status();data=r.json()
    except (httpx.HTTPError,ValueError): raise HTTPException(502,'Não foi possível gerar a prévia do Figma.')
    url=(data.get('images') or {}).get(node_id)
    if not isinstance(url,str) or not url.startswith('https://'): raise HTTPException(404,'A camada não pôde ser renderizada.')
    return {'file_key':file_key,'node_id':node_id,'image_url':url,'note':'A URL é temporária e fornecida pelo Figma.'}
