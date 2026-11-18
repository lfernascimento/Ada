import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.tools import render_sandbox as rs
from app.tools.workspace_sandbox import open_workspace, validate_workspace


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv('ADA_SANDBOX_BACKEND', 'render')
    monkeypatch.setenv('ADA_RENDER_SANDBOX_ENABLED', 'true')
    monkeypatch.setenv('RENDER_API_KEY', 'server-only-test-key')
    monkeypatch.setenv('RENDER_WORKSPACE_ID', 'test-workspace')
    monkeypatch.setattr(rs, '_cleanup_failed', False)
    class Fake:
        def __init__(self):
            self.calls = []; self.uploads = []; self.fail_at = None; self.created = 0
            self.events = [SimpleNamespace(stream='stdout', data=json.dumps({'status':'passed','message':'ok','checks':[]})), SimpleNamespace(exit_code=0)]
        async def create(self, **kw):
            self.calls.append(('create', kw)); self.created += 1
            return SimpleNamespace(id=f'sbx-test-{self.created}',network_policy=SimpleNamespace(type='deny-all'),plan='1c-2g',timeout_seconds=300)
        async def wait_until_running(self, sid, **kw):
            self.calls.append(('wait', sid))
            if self.fail_at == 'wait': raise ValueError('provider error including server-only-test-key')
        async def copy_to(self, sid, local, dest):
            self.uploads.append((sid, dest, Path(local).read_bytes()))
            if self.fail_at == 'upload': raise RuntimeError('server-only-test-key')
        async def exec(self, sid, command):
            self.calls.append(('exec', sid, command))
            for event in self.events: yield event
        async def terminate(self, sid):
            self.calls.append(('terminate', sid))
            if self.fail_at == 'terminate': raise ValueError('secret')
    fake = Fake()
    monkeypatch.setattr(rs, '_client', lambda: SimpleNamespace(sandboxes=fake))
    return fake


def test_validation_uses_disposable_guests_and_no_credentials(remote):
    with open_workspace(b'archive') as w:
        assert w.validate([{'path':'x.py','content':'x=1'}])['status'] == 'passed'
        assert w.validate([{'path':'x.py','content':'x=2'}])['status'] == 'passed'
    create = [c[1] for c in remote.calls if c[0]=='create']
    assert len(create) == 2
    assert all(c['network_policy']=='deny-all' and c['env']=={} and c['timeout_seconds']==300 for c in create)
    assert all(c['plan']=='1c-2g' for c in create)
    assert ('terminate','sbx-test-1') in remote.calls and ('terminate','sbx-test-2') in remote.calls
    assert all(b'server-only-test-key' not in data for _,_,data in remote.uploads)
    assert {dest for _,dest,_ in remote.uploads} == {'/tmp/ada-runner.py','/tmp/ada-input.json'}


@pytest.mark.parametrize('stage',['wait','upload'])
def test_provider_failure_cleans_guest_and_does_not_leak_key(remote,stage):
    remote.fail_at = stage
    result = validate_workspace([{'path':'x.py','content':'x=1'}], b'archive')
    assert result['status']=='unavailable'
    assert 'server-only-test-key' not in str(result)
    assert ('terminate','sbx-test-1') in remote.calls


@pytest.mark.parametrize('events',[
    [SimpleNamespace(stream='stdout',data='x'*65537)],
    [SimpleNamespace(stream='stdout',data='not json'),SimpleNamespace(exit_code=0)],
    [SimpleNamespace(stream='stdout',data='{}')],
    [SimpleNamespace(exit_code=1)],
])
def test_invalid_or_unbounded_output_never_passes(remote,events):
    remote.events=events
    result=validate_workspace([{'path':'x.py','content':'x=1'}],b'archive')
    assert result['status']=='unavailable'
    assert ('terminate','sbx-test-1') in remote.calls


def test_cleanup_failure_blocks_new_runs(remote):
    remote.fail_at='terminate'
    result=validate_workspace([{'path':'x.py','content':'x=1'}],b'archive')
    assert result['status']=='unavailable' and 'Limpeza' in result['message']
    remote.fail_at=None
    assert validate_workspace([{'path':'x.py','content':'x=1'}],b'archive')['status']=='unavailable'
    assert remote.created==1


def test_disabled_executor_does_not_create_resources(remote,monkeypatch):
    monkeypatch.setenv('ADA_RENDER_SANDBOX_ENABLED','false')
    assert validate_workspace([{'path':'x.py','content':'x=1'}],b'archive')['status']=='unavailable'
    assert not remote.calls


def test_snippet_dispatch(remote):
    from app.tools.sandbox import execute_python, PythonSandboxArgs
    remote.events=[SimpleNamespace(stream='stdout',data='4\n'),SimpleNamespace(exit_code=0)]
    result=execute_python(PythonSandboxArgs(codigo='print(2+2)'))
    assert result=={'saida':'4\n','codigo_saida':0}
    command=next(c[2] for c in remote.calls if c[0]=='exec')
    assert command.startswith('timeout -k 2s 15s python3 -I -B -c ')
    assert ('terminate','sbx-test-1') in remote.calls


def test_provider_must_confirm_network_isolation(remote):
    async def wrong_policy(**kw):
        return SimpleNamespace(id='sbx-test-1',network_policy=SimpleNamespace(type='allow-all'),plan='1c-2g',timeout_seconds=300)
    remote.create=wrong_policy
    assert validate_workspace([{'path':'x.py','content':'x=1'}],b'archive')['status']=='unavailable'
    assert not any(c[0]=='exec' for c in remote.calls)
    assert ('terminate','sbx-test-1') in remote.calls


def test_official_sdk_surface_is_compatible():
    import inspect
    from render import RenderAsync
    from render.sandbox import SandboxExecOutput, SandboxExecExit
    client=RenderAsync(token='test-only',owner_id='test-workspace').sandboxes
    inspect.signature(client.create).bind(name='ada-validation',plan='1c-2g',timeout_seconds=300,idle_timeout_seconds=300,network_policy='deny-all',env={},wait=False)
    inspect.signature(client.copy_to).bind('sbx-test','input.json','/tmp/input.json')
    assert SandboxExecOutput(stream='stdout',data='4').data=='4'
    assert SandboxExecExit(exit_code=0).exit_code==0
