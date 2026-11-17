"""Execução manual explícita em sandbox Docker local; não é executor remoto."""
import asyncio,shutil,subprocess
from fastapi import APIRouter,Depends,HTTPException
from .connections import local_only
from .tools.sandbox import PythonSandboxArgs,SandboxError,execute_python

router=APIRouter(prefix='/sandbox',dependencies=[Depends(local_only)])

def inspect_docker():
    binary=shutil.which('docker')
    if not binary:return {'docker':False,'engine':False,'image':False,'message':'Docker não encontrado.'}
    try:
        engine=subprocess.run([binary,'info','--format','{{.ServerVersion}}'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=8,check=False)
        if engine.returncode:return {'docker':True,'engine':False,'image':False,'message':'Não foi possível acessar o Docker Engine.'}
        image=subprocess.run([binary,'image','inspect','python:3.12-alpine','--format','{{.Id}}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8,check=False)
        return {'docker':True,'engine':True,'image':image.returncode==0,'message':'Docker disponível.' if image.returncode==0 else 'Imagem python:3.12-alpine não encontrada.'}
    except (OSError,subprocess.SubprocessError): return {'docker':True,'engine':False,'image':False,'message':'Não foi possível verificar o Docker.'}

@router.get('/status')
async def status(): return await asyncio.to_thread(inspect_docker)

@router.post('/execute')
async def execute(body:PythonSandboxArgs):
    try:return await asyncio.to_thread(execute_python,body)
    except SandboxError as e: raise HTTPException(400,str(e)[:500])
