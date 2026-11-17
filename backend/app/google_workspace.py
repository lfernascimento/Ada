"""Consultas locais e explícitas, somente leitura, às APIs Google Workspace."""
import re,time,base64,json,uuid
from email.message import EmailMessage
from pathlib import PurePosixPath
from datetime import datetime,timezone
from urllib.parse import urlsplit,quote
import httpx
from fastapi import APIRouter,Depends,HTTPException,Response,Request
from pydantic import BaseModel,ConfigDict,Field
from .connections import local_only,_tokens,GOOGLE_WRITE_SCOPES

router=APIRouter(prefix='/workspace',dependencies=[Depends(local_only)])

def session(service):
    item=_tokens.get(service)
    if not item or (item.get('expires_at') and item['expires_at']<=time.time()): raise HTTPException(401,'Conecte este serviço novamente.')
    return item['access_token']

def file_id(value):
    value=value.strip()
    if value.startswith('https://'):
        url=urlsplit(value)
        if url.hostname not in ('docs.google.com','drive.google.com'): raise HTTPException(422,'Informe uma URL Google válida.')
        match=re.search(r'/d/([A-Za-z0-9_-]+)',url.path)
        if not match: raise HTTPException(422,'Não foi possível identificar o arquivo na URL.')
        value=match.group(1)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',value): raise HTTPException(422,'ID do arquivo inválido.')
    return value

async def get(client,url,params=None):
    try:
        r=await client.get(url,params=params);r.raise_for_status();return r.json()
    except (httpx.HTTPError,ValueError): raise HTTPException(502,'Não foi possível consultar o Google. Verifique a permissão e o arquivo.')

def extract_text(node,parts):
    if isinstance(node,dict):
        text=node.get('textRun',{}).get('content') if isinstance(node.get('textRun'),dict) else None
        if isinstance(text,str): parts.append(text)
        for v in node.values(): extract_text(v,parts)
    elif isinstance(node,list):
        for v in node: extract_text(v,parts)

@router.get('/{service}/browse')
async def browse(service:str,response:Response,id:str='',range:str='A1:Z50'):
    if service not in ('drive','docs','sheets','gmail','calendar','classroom'): raise HTTPException(404,'Serviço não encontrado.')
    token=session(service);response.headers['Cache-Control']='no-store'
    headers={'Authorization':'Bearer '+token,'Accept':'application/json','User-Agent':'Ada-local'}
    async with httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False,headers=headers) as client:
        if service=='drive':
            d=await get(client,'https://www.googleapis.com/drive/v3/files',{'pageSize':30,'fields':'files(id,name,mimeType,modifiedTime)','q':'trashed = false'})
            return {'service':service,'items':d.get('files',[]),'note':'Somente arquivos acessíveis no escopo drive.file.'}
        if service=='docs':
            doc=file_id(id);d=await get(client,'https://docs.googleapis.com/v1/documents/'+quote(doc,safe=''),{'fields':'title,body.content'})
            parts=[];extract_text(d.get('body',{}),parts)
            text=''.join(parts);return {'service':service,'title':d.get('title','Documento'),'text':text[:30000],'truncated':len(text)>30000}
        if service=='sheets':
            doc=file_id(id)
            match=re.fullmatch(r"(?:[A-Za-z0-9_ .'-]{1,60}!)?([A-Za-z]{1,2})([1-9][0-9]{0,6})(?::([A-Za-z]{1,2})([1-9][0-9]{0,6}))?",range)
            if not match: raise HTTPException(422,'Informe um intervalo A1 válido de até 50 linhas e 26 colunas.')
            def col(x):
                result=0
                for ch in x.upper(): result=result*26+ord(ch)-64
                return result
            c1,r1,c2,r2=match.groups(); c2=c2 or c1; r2=r2 or r1
            if int(r2)<int(r1) or int(r2)-int(r1)>=50 or col(c2)<col(c1) or col(c2)-col(c1)>=26:
                raise HTTPException(422,'O intervalo deve ter até 50 linhas e 26 colunas.')
            d=await get(client,'https://sheets.googleapis.com/v4/spreadsheets/'+quote(doc,safe='')+'/values/'+quote(range,safe=''))
            rows=d.get('values',[])
            return {'service':service,'range':d.get('range',range),'values':[[str(cell)[:500] for cell in row[:26]] for row in rows[:50]],'truncated':len(rows)>50}
        if service=='calendar':
            d=await get(client,'https://www.googleapis.com/calendar/v3/calendars/primary/events',{'maxResults':20,'singleEvents':'true','orderBy':'startTime','timeMin':datetime.now(timezone.utc).isoformat()})
            return {'service':service,'items':[{'summary':x.get('summary','(Sem título)'),'start':x.get('start'),'end':x.get('end')} for x in d.get('items',[])]}
        if service=='classroom':
            d=await get(client,'https://classroom.googleapis.com/v1/courses',{'pageSize':30,'courseStates':'ACTIVE'})
            return {'service':service,'items':[{'id':x.get('id'),'name':x.get('name'),'section':x.get('section')} for x in d.get('courses',[])]}
        listing=await get(client,'https://gmail.googleapis.com/gmail/v1/users/me/messages',{'maxResults':10,'labelIds':'INBOX'})
        items=[]
        for item in listing.get('messages',[])[:10]:
            mid=item.get('id','')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',mid): continue
            d=await get(client,'https://gmail.googleapis.com/gmail/v1/users/me/messages/'+mid,{'format':'metadata','metadataHeaders':['Subject','From','Date']})
            headers_out={x.get('name',''):x.get('value','') for x in d.get('payload',{}).get('headers',[]) if x.get('name','').lower() in ('subject','from','date')}
            items.append({'id':mid,'headers':headers_out})
        return {'service':service,'items':items,'note':'Somente cabeçalhos; corpo das mensagens não é acessado.'}


def write_session(service):
    token=session(service)
    granted=set((_tokens[service].get('scopes') or '').replace(',',' ').split())
    scope=GOOGLE_WRITE_SCOPES.get(service)
    if not scope or scope not in granted:
        raise HTTPException(403,'Autorize a criação neste serviço antes de continuar.')
    return token

async def post(client,url,**kwargs):
    try:
        r=await client.post(url,**kwargs);r.raise_for_status();return r.json()
    except (httpx.HTTPError,ValueError):
        raise HTTPException(502,'O Google não confirmou a criação. Verifique o serviço antes de tentar novamente.')

class CreateItem(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=150)
    content:str=Field(default='',max_length=30000)
    recipient:str=Field(default='',max_length=254)
    start:str=Field(default='',max_length=50)
    end:str=Field(default='',max_length=50)

@router.post('/drive/upload')
async def upload_drive(request:Request,response:Response,name:str=''):
    token=write_session('drive')
    name=PurePosixPath(name.replace('\\','/')).name
    if not name or len(name)>180 or any(ord(ch)<32 for ch in name): raise HTTPException(422,'Nome de arquivo inválido.')
    buf=bytearray()
    async for part in request.stream():
        buf.extend(part)
        if len(buf)>5_000_000: raise HTTPException(413,'O arquivo deve ter no máximo 5 MB.')
    if not buf: raise HTTPException(422,'Selecione um arquivo não vazio.')
    boundary='ada-'+uuid.uuid4().hex
    metadata=json.dumps({'name':name},ensure_ascii=False).encode('utf-8')
    payload=(('--'+boundary+'\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n').encode()+metadata+
             ('\r\n--'+boundary+'\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+bytes(buf)+('\r\n--'+boundary+'--\r\n').encode())
    headers={'Authorization':'Bearer '+token,'Accept':'application/json','Content-Type':'multipart/related; boundary='+boundary}
    async with httpx.AsyncClient(timeout=30,trust_env=False,follow_redirects=False,headers=headers) as client:
        data=await post(client,'https://www.googleapis.com/upload/drive/v3/files',params={'uploadType':'multipart','fields':'id,name,mimeType,webViewLink'},content=payload)
    response.headers['Cache-Control']='no-store'
    return {'service':'drive','id':data.get('id'),'name':data.get('name')}

@router.post('/{service}/create')
async def create(service:str,body:CreateItem,response:Response):
    if service not in ('docs','sheets','gmail','calendar','classroom'): raise HTTPException(404,'Serviço não encontrado.')
    if not body.title.strip(): raise HTTPException(422,'Informe um título.')
    token=write_session(service)
    headers={'Authorization':'Bearer '+token,'Accept':'application/json','User-Agent':'Ada-local'}
    response.headers['Cache-Control']='no-store'
    async with httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False,headers=headers) as client:
        if service=='docs':
            data=await post(client,'https://docs.googleapis.com/v1/documents',json={'title':body.title})
            docid=data.get('documentId','')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',docid): raise HTTPException(502,'Documento criado, mas o Google não retornou um identificador válido; confira antes de repetir.')
            if body.content:
                try: await post(client,'https://docs.googleapis.com/v1/documents/'+docid+':batchUpdate',json={'requests':[{'insertText':{'location':{'index':1},'text':body.content}}]})
                except HTTPException: raise HTTPException(502,'O documento '+docid+' foi criado, mas o conteúdo não foi confirmado. Verifique no Google Docs antes de repetir.')
            return {'service':service,'id':docid,'title':body.title}
        if service=='sheets':
            lines=body.content.splitlines()[:50]
            rows=[{'values':[{'userEnteredValue':{'stringValue':cell[:500]}} for cell in line.split('\t')[:26]]} for line in lines]
            payload={'properties':{'title':body.title},'sheets':[{'data':[{'rowData':rows}]}]}
            data=await post(client,'https://sheets.googleapis.com/v4/spreadsheets',json=payload)
            return {'service':service,'id':data.get('spreadsheetId'),'title':body.title}
        if service=='gmail':
            msg=EmailMessage()
            try:
                msg['Subject']=body.title
                if body.recipient: msg['To']=body.recipient
                msg.set_content(body.content)
            except (ValueError,TypeError): raise HTTPException(422,'Destinatário ou assunto inválido.')
            raw=base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')
            data=await post(client,'https://gmail.googleapis.com/gmail/v1/users/me/drafts',json={'message':{'raw':raw}})
            return {'service':service,'id':data.get('id'),'title':body.title,'status':'rascunho'}
        if service=='calendar':
            try:
                start=datetime.fromisoformat(body.start.replace('Z','+00:00'));end=datetime.fromisoformat(body.end.replace('Z','+00:00'))
                if not start.tzinfo or not end.tzinfo or end<=start: raise ValueError()
            except ValueError: raise HTTPException(422,'Informe início e fim com fuso horário; o fim deve ser posterior ao início.')
            payload={'summary':body.title,'description':body.content,'start':{'dateTime':start.isoformat()},'end':{'dateTime':end.isoformat()}}
            data=await post(client,'https://www.googleapis.com/calendar/v3/calendars/primary/events',params={'sendUpdates':'none'},json=payload)
            return {'service':service,'id':data.get('id'),'title':data.get('summary',body.title)}
        data=await post(client,'https://classroom.googleapis.com/v1/courses',json={'name':body.title,'descriptionHeading':body.content[:3600],'ownerId':'me','courseState':'PROVISIONED'})
        return {'service':service,'id':data.get('id'),'title':data.get('name',body.title),'status':'não ativada'}
