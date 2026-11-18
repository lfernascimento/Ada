"""Local single-user boundary. Public/multi-user deployment is intentionally unsupported."""
import re,os,base64,hmac,hashlib
from urllib.parse import urlsplit
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

TRUSTED_HOSTS={'localhost','127.0.0.1','::1'}
TRUSTED_PEERS={'127.0.0.1','::1'}
class LocalBoundary(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        mode=os.getenv('ADA_DEPLOYMENT_MODE','local')
        if mode=='private_cloud':return await private_cloud(request,call_next)
        if mode!='local':return JSONResponse({'detail':'Modo de instalação inválido.'},status_code=503)
        host=request.url.hostname
        peer=request.client.host if request.client else ''
        if host not in TRUSTED_HOSTS or peer not in TRUSTED_PEERS:
            return JSONResponse({'detail':'Esta instalação aceita somente acesso local.'},status_code=403)
        origin=request.headers.get('origin')
        if origin and not re.fullmatch(r'http://(?:localhost|127\.0\.0\.1):(?:517[3-9]|8000)',origin):
            return JSONResponse({'detail':'Origem não autorizada.'},status_code=403)
        # OAuth providers return through a top-level browser navigation. Only
        # these exact GET callbacks may cross sites; handlers still validate
        # the single-use state, expiry and provider before exchanging any code.
        oauth_navigation=(request.method=='GET'
            and re.fullmatch(r'/connections/(?:github|figma|drive)/callback',request.url.path) is not None
            and request.headers.get('sec-fetch-mode')=='navigate'
            and request.headers.get('sec-fetch-dest')=='document')
        if request.headers.get('sec-fetch-site')=='cross-site' and not oauth_navigation:
            return JSONResponse({'detail':'Solicitação de outro site bloqueada.'},status_code=403)
        return await call_next(request)

async def private_cloud(request,call_next):
    origin=os.getenv('ADA_PUBLIC_ORIGIN','').rstrip('/')
    parsed=urlsplit(origin)
    password=os.getenv('ADA_ACCESS_PASSWORD','')
    username=os.getenv('ADA_ACCESS_USER','ada')
    if parsed.scheme!='https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or len(password)<8 or os.getenv('ADA_DATABASE_BACKEND')!='postgres':
        return JSONResponse({'detail':'Instalação privada ainda não configurada.'},status_code=503)
    if request.url.netloc!=parsed.netloc:return JSONResponse({'detail':'Host não autorizado.'},status_code=403)
    if request.url.path=='/health' and request.method=='GET':return JSONResponse({'status':'ok'})
    authenticated=False
    try:
        scheme,value=request.headers.get('authorization','').split(' ',1)
        supplied=base64.b64decode(value,validate=True).decode('utf-8')
        authenticated=scheme.lower()=='basic' and hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),hashlib.sha256((username+':'+password).encode()).digest())
    except (ValueError,UnicodeError):pass
    if not authenticated:return JSONResponse({'detail':'Autenticação necessária.'},status_code=401,headers={'WWW-Authenticate':'Basic realm="Ada privada", charset="UTF-8"','Cache-Control':'no-store'})
    supplied_origin=request.headers.get('origin')
    callback=(request.method=='GET' and re.fullmatch(r'/connections/(github|figma|drive)/callback',request.url.path) is not None and request.headers.get('sec-fetch-mode')=='navigate' and request.headers.get('sec-fetch-dest')=='document')
    if (supplied_origin and supplied_origin!=origin) or (request.headers.get('sec-fetch-site')=='cross-site' and not callback):return JSONResponse({'detail':'Origem não autorizada.'},status_code=403)
    # The development proxy and production service expose the same /api prefix.
    if request.url.path.startswith('/api/'):
        request.scope['path']=request.scope['path'][4:]
        request.scope['raw_path']=request.scope['path'].encode('utf-8')
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Cache-Control']='no-store'
    return response
