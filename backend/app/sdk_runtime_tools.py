"""General task capabilities exposed to the native SDK, scoped to one conversation."""
import asyncio,json
from urllib.parse import quote
from claude_agent_sdk import tool

def runtime_tools(aid):
    from .sdk_workspace import current,safe_name
    from .sdk_inputs import ask
    from .model_gateway import active_native
    def context():
        w=current.get();native=active_native.get()
        if not w or w.aid!=aid or not native:raise ValueError('Área de trabalho indisponível.')
        return w,native[0]
    def output(data):return {'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}]}

    @tool('perguntar_usuario','Pede uma informação necessária para continuar e aguarda a resposta no chat.',{'question':str})
    async def question(args):
        w,rid=context()
        result=await ask(aid,w.cid,rid,str(args['question']))
        return output({'answer':result.answer})

    @tool('entregar_arquivo','Publica um link de download de um arquivo produzido nesta área de trabalho. Não envia a serviços externos.',{'path':str})
    async def deliver(args):
        w,_=context()
        name=safe_name(args['path'])
        if not (w.path/name).is_file():return {'isError':True,'content':[{'type':'text','text':'O arquivo não existe.'}]}
        await asyncio.to_thread(w.save)
        return output({'path':name,'url':f'/api/agents/{aid}/conversations/{w.cid}/workspace/file?path='+quote(name,safe='')})

    @tool('executar_python','Executa Python isolado com biblioteca padrão e até 3 arquivos desta conversa, após aprovação. arquivos recebe caminhos relativos; grave entregas em outputs/ (até 4 arquivos e 24 KB no total).',{'type':'object','properties':{'codigo':{'type':'string','maxLength':12000},'arquivos':{'type':'array','items':{'type':'string'},'maxItems':3}},'required':['codigo'],'additionalProperties':False})
    async def python(args):
        from .repo import get_active_spec
        from .tools.sandbox import PythonSandboxArgs
        from .tools.analysis_sandbox import execute
        w,rid=context()
        body=PythonSandboxArgs(codigo=args['codigo'])
        names=args.get('arquivos',[])
        if not isinstance(names,list) or len(names)>3:raise ValueError('Até três arquivos por execução.')
        import base64
        files={}
        for name in names:
            name=safe_name(name)
            path=w.path/name
            if not w.allowed('Read',{'file_path':str(path)}) or not path.is_file() or path.is_symlink() or path.stat().st_size>512000:raise ValueError('Arquivo indisponível ou maior que 512 KB.')
            files[name]=base64.b64encode(path.read_bytes()).decode()
        if sum(len(v) for v in files.values())>1_400_000:raise ValueError('Arquivos muito grandes para esta execução.')
        if get_active_spec(aid).autonomy_policy.get('executar_codigo')=='denied':
            return {'isError':True,'content':[{'type':'text','text':'Execução negada pelo perfil.'}]}
        answer=await ask(aid,w.cid,rid,'Executar este código na sandbox? O executor remoto pode gerar cobrança.',approval=True,detail=body.codigo+'\n\nArquivos: '+(', '.join(files) or 'nenhum'))
        if not answer.approved:return output({'executed':False,'reason':'Execução não autorizada pelo usuário.'})
        if get_active_spec(aid).autonomy_policy.get('executar_codigo')=='denied':raise ValueError('Permissão revogada.')
        result=await asyncio.to_thread(execute,body.codigo,files)
        for name,data in result.pop('files').items():
            path=w.path/safe_name(name)
            if not w.allowed('Write',{'file_path':str(path)}):raise ValueError('Destino fora da área de trabalho.')
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(base64.b64decode(data,validate=True))
        result['files']=[name for name in w.capture() if name.startswith('outputs/')]
        return output(result)
    return [question,deliver,python]
