import json
from .db import connect, begin_write, insert_id
from .models import AgentSpec

def create_agent(spec):
    with connect() as c:
        agent_id=insert_id(c,'INSERT INTO agents(name,active_version) VALUES(?,1)',(spec.name,))
        c.execute('INSERT INTO agent_versions(agent_id,number,spec_json,reason,origin) VALUES(?,1,?,?,?)',(agent_id,spec.model_dump_json(),'Criação','usuário'))
        return agent_id

def add_version(agent_id,spec,reason,origin):
    with connect() as c:
        begin_write(c)
        if not c.execute('SELECT 1 FROM agents WHERE id=?',(agent_id,)).fetchone(): raise KeyError(agent_id)
        n=c.execute('SELECT COALESCE(MAX(number),0)+1 FROM agent_versions WHERE agent_id=?',(agent_id,)).fetchone()[0]
        c.execute('INSERT INTO agent_versions(agent_id,number,spec_json,reason,origin) VALUES(?,?,?,?,?)',(agent_id,n,spec.model_dump_json(),reason,origin)); return n

def activate_version(agent_id,number):
    with connect() as c:
        row=c.execute('SELECT spec_json FROM agent_versions WHERE agent_id=? AND number=?',(agent_id,number)).fetchone()
        if not row: raise KeyError(number)
        c.execute('UPDATE agents SET active_version=?,name=? WHERE id=?',(number,json.loads(row[0])['name'],agent_id))

def get_active_spec(agent_id):
    with connect() as c:
        row=c.execute('SELECT v.spec_json FROM agents a JOIN agent_versions v ON v.agent_id=a.id AND v.number=a.active_version WHERE a.id=?',(agent_id,)).fetchone()
        if not row: raise KeyError(agent_id)
        return AgentSpec.model_validate_json(row[0])

def list_agents():
    with connect() as c:
        rows=c.execute('SELECT a.*,v.spec_json FROM agents a JOIN agent_versions v ON v.agent_id=a.id AND v.number=a.active_version ORDER BY a.id').fetchall()
        return [dict(id=r['id'],name=r['name'],color=json.loads(r['spec_json'])['style']['color'],avatar=json.loads(r['spec_json'])['style'].get('avatar','classic'),active_version=r['active_version']) for r in rows]

def diff_specs(a,b):
    left=a.model_dump(); right=b.model_dump()
    return [dict(field=k,before=left[k],after=right[k]) for k in left if left[k]!=right[k]]
