"""Approval-gated, disposable Docker execution for small Python snippets.

Docker is an isolation layer, not a complete hostile-code security boundary. Never
mount a workspace, host sockets, credentials, or network into this container.
"""
import os, shutil, subprocess, threading, uuid
from pydantic import BaseModel, ConfigDict, Field

class PythonSandboxArgs(BaseModel):
    model_config=ConfigDict(extra='forbid')
    codigo: str=Field(min_length=1,max_length=12000)

class SandboxError(Exception): pass

def execute_python(args:PythonSandboxArgs):
    docker=shutil.which('docker')
    if not docker: raise SandboxError('Docker não está instalado ou não está no PATH.')
    # No implicit download; installation and image provenance are user-controlled.
    image='python:3.12-alpine'; name='ada-sandbox-'+uuid.uuid4().hex
    cmd=[docker,'run','--name',name,'--rm','--pull=never','--network=none','--read-only',
         '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=64','--memory=128m',
         '--cpus=0.5','--user=65534:65534','--tmpfs=/tmp:rw,noexec,nosuid,size=16m',
         '--env','HTTP_PROXY=','--env','HTTPS_PROXY=','--env','ALL_PROXY=',
         '--env','http_proxy=','--env','https_proxy=','--env','all_proxy=',
         '-i',image,'python','-I','-B','-c','import sys; exec(compile(sys.stdin.read(), "<sandbox>", "exec"))']
    # Avoid unbounded output from hostile snippets. Docker CLI captures at most 64 KB.
    proc=None; output=bytearray(); overflow=threading.Event()
    try:
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        def send():
            try: proc.stdin.write(args.codigo.encode('utf-8')); proc.stdin.close()
            except (BrokenPipeError,OSError,ValueError): pass
        threading.Thread(target=send,daemon=True).start()
        def read():
            while True:
                block=proc.stdout.read(4096)
                if not block: break
                left=65536-len(output)
                if left>0: output.extend(block[:left])
                if len(block)>left:
                    overflow.set();proc.kill();break
        thread=threading.Thread(target=read,daemon=True);thread.start()
        try: code=proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=3); raise SandboxError('Tempo limite da sandbox atingido.')
        thread.join(timeout=2)
        if overflow.is_set(): raise SandboxError('A saída excedeu o limite de 64 KB.')
        result=output.decode('utf-8',errors='replace')
        if code!=0: raise SandboxError('Execução falhou. Verifique Docker, a imagem local e o código. Saída: '+result[:1500])
        return {'saida':result,'codigo_saida':code}
    except (OSError,subprocess.SubprocessError) as exc: raise SandboxError('Não foi possível iniciar a sandbox.') from exc
    finally:
        # Always attempt cleanup, including after CLI timeout or output overflow.
        try: subprocess.run([docker,'rm','-f',name],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
        except (OSError,subprocess.SubprocessError): pass
