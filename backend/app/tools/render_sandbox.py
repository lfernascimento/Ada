"""Opt-in Render executor. Credentials stay in the control plane, never in the guest."""
import asyncio
import base64
import importlib.util
import json
import os
import shlex
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_slot = threading.BoundedSemaphore(1)
_cleanup_failed = False
MAX_OUTPUT = 65536


class RemoteSandboxError(RuntimeError):
    pass


def configuration():
    return (os.getenv('ADA_RENDER_SANDBOX_ENABLED', '').lower() == 'true'
            and bool(os.getenv('RENDER_API_KEY', '').strip())
            and bool(os.getenv('RENDER_WORKSPACE_ID', '').strip()))


def inspect_render():
    ready = configuration() and importlib.util.find_spec('render') is not None and not _cleanup_failed
    return {'provider': 'render', 'configured': ready,
            'message': ('Render configurado; a execuÃ§Ã£o real ainda precisa ser validada e gera cobranÃ§a.' if ready
                        else 'Sandbox Render indisponÃ­vel: confira habilitaÃ§Ã£o, credencial, workspace e SDK; se houve falha de limpeza, verifique o painel antes de reiniciar o backend.')}


def _client():
    from render import RenderAsync
    return RenderAsync(token=os.environ['RENDER_API_KEY'], owner_id=os.environ['RENDER_WORKSPACE_ID'])


async def _run(command, uploads):
    global _cleanup_failed
    client = _client()
    sandbox_id = None
    try:
        async with asyncio.timeout(150):
            # Save the ID before waiting: readiness failure must still trigger termination.
            sandbox = await client.sandboxes.create(name='ada-validation', plan='1c-2g', timeout_seconds=300,
                idle_timeout_seconds=300, network_policy='deny-all', env={}, wait=False)
            sandbox_id = sandbox.id
            if sandbox.network_policy.type != 'deny-all' or sandbox.plan != '1c-2g' or not 0 < sandbox.timeout_seconds <= 300:
                raise RemoteSandboxError('O Render não confirmou os limites de isolamento e duração solicitados.')
            await client.sandboxes.wait_until_running(sandbox_id, timeout_seconds=45)
            for local, remote in uploads:
                await client.sandboxes.copy_to(sandbox_id, str(local), remote)
            output, stderr, size, exit_code = [], [], 0, None
            async for event in client.sandboxes.exec(sandbox_id, command):
                if hasattr(event, 'exit_code'):
                    exit_code = event.exit_code
                elif hasattr(event, 'data'):
                    size += len(event.data.encode('utf-8'))
                    if size > MAX_OUTPUT:
                        raise RemoteSandboxError('A saÃ­da da sandbox excedeu 64 KB.')
                    (output if event.stream == 'stdout' else stderr).append(event.data)
            if exit_code is None:
                raise RemoteSandboxError('A sandbox nÃ£o confirmou o encerramento do comando.')
            return exit_code, ''.join(output), ''.join(stderr)
    finally:
        if sandbox_id:
            try:
                await asyncio.wait_for(client.sandboxes.terminate(sandbox_id), timeout=20)
            except Exception:
                _cleanup_failed = True
                raise RemoteSandboxError('Limpeza nÃ£o confirmada na sandbox Render. Verifique o painel antes de executar novamente.') from None


def _execute(command, uploads=()):
    if not configuration():
        raise RemoteSandboxError('Configure e habilite a sandbox Render no servidor antes de executar.')
    if _cleanup_failed:
        raise RemoteSandboxError('Verifique a limpeza da sandbox no painel antes de reiniciar o backend.')
    if not _slot.acquire(blocking=False):
        raise RemoteSandboxError('Outra tarefa estÃ¡ usando a sandbox Render; tente novamente.')
    try:
        return asyncio.run(_run(command, uploads))
    except RemoteSandboxError:
        raise
    except Exception:
        # Provider errors can contain request headers and API credentials.
        raise RemoteSandboxError('NÃ£o foi possÃ­vel executar no Render. Confira credencial, saldo, workspace e disponibilidade no painel.') from None
    finally:
        _slot.release()


def execute_python(code):
    if not isinstance(code, str) or not 1 <= len(code) <= 12000:
        raise RemoteSandboxError('CÃ³digo Python fora dos limites da sandbox.')
    encoded = base64.b64encode(code.encode()).decode('ascii')
    loader = "import base64;exec(compile(base64.b64decode('" + encoded + "'),'<sandbox>','exec'))"
    command = 'timeout -k 2s 15s python3 -I -B -c ' + shlex.quote(loader)
    status, output, _ = _execute(command)
    if status:
        raise RemoteSandboxError('O cÃ³digo nÃ£o concluiu com sucesso na sandbox Render (erro ou tempo limite).')
    return {'saida': output, 'codigo_saida': status}


class RenderWorkspace:
    """One fresh remote guest per validation; archives and final edits stay in Ada."""
    def __init__(self, archive):
        if not archive or len(archive) > 8 * 1024 * 1024:
            raise RemoteSandboxError('CÃ³pia do repositÃ³rio ausente ou acima de 8 MB.')
        self.archive = archive

    def __enter__(self):
        if not configuration():
            raise RemoteSandboxError('Configure e habilite a sandbox Render no servidor antes de executar.')
        return self

    def validate(self, files):
        from .workspace_sandbox import _payload
        try:
            with tempfile.TemporaryDirectory(prefix='ada-render-') as directory:
                payload = Path(directory) / 'input.json'
                payload.write_bytes(_payload(files, self.archive))
                status, output, _ = _execute('python3 -I -B /tmp/ada-runner.py /tmp/ada-input.json',
                    [(ROOT / 'sandbox/runner.py', '/tmp/ada-runner.py'), (payload, '/tmp/ada-input.json')])
                if status:
                    raise ValueError('runner failed')
                result = json.loads(output)
                if (not isinstance(result, dict) or result.get('status') not in ('passed', 'failed', 'not_applicable', 'unavailable')
                        or not isinstance(result.get('checks'), list) or len(result['checks']) > 10):
                    raise ValueError('invalid runner response')
                return result
        except RuntimeError as exc:
            return {'status': 'unavailable', 'message': str(exc), 'checks': []}
        except (OSError, ValueError):
            return {'status': 'unavailable', 'message': 'A sandbox Render nÃ£o retornou uma validaÃ§Ã£o compatÃ­vel.', 'checks': []}

    def __exit__(self, *args):
        pass  # Every _execute has already terminated its guest before returning.
