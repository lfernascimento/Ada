"""Conversation-scoped implementation of the SDK's native SessionStore protocol."""
import asyncio,json,uuid
from .db import connect,begin_write

MAX_TRANSCRIPT=64*1024*1024

class ConversationStore:
    def __init__(self,agent_id,cid):
        self.agent_id=agent_id;self.cid=cid;self.failed=False

    def owner(self,c):
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND agent_id=?',(self.cid,self.agent_id)).fetchone():
            raise ValueError('Conversa inexistente.')

    async def append(self,key,entries):
        try:await asyncio.to_thread(self._append,key,entries)
        except Exception:
            self.failed=True
            raise RuntimeError('Não foi possível persistir a sessão do agente.') from None

    def _append(self,key,entries):
        encoded=[(str(e.get('uuid') or uuid.uuid4()),json.dumps(e,ensure_ascii=False)) for e in entries]
        if sum(len(e.encode()) for _,e in encoded)>MAX_TRANSCRIPT:raise ValueError('Sessão muito grande.')
        sid=str(key['session_id']);sub=str(key.get('subpath') or '');project=str(key.get('project_key',''))
        with connect() as c:
            begin_write(c);self.owner(c)
            current=c.execute('SELECT COALESCE(SUM(length(entry_json)),0) FROM sdk_session_entries WHERE conversation_id=?',(self.cid,)).fetchone()[0]
            if 4*(current+sum(len(e) for _,e in encoded))>MAX_TRANSCRIPT:raise ValueError('Limite de persistência da sessão.')
            seq=c.execute('SELECT COALESCE(MAX(seq),0) FROM sdk_session_entries WHERE conversation_id=? AND project_key=? AND session_id=? AND subpath=?',(self.cid,project,sid,sub)).fetchone()[0]
            for uid,text in encoded:
                seq+=1
                c.execute('INSERT INTO sdk_session_entries(conversation_id,project_key,session_id,subpath,seq,entry_uid,entry_json) VALUES(?,?,?,?,?,?,?) ON CONFLICT(conversation_id,project_key,session_id,subpath,entry_uid) DO NOTHING',(self.cid,project,sid,sub,seq,uid,text))

    async def load(self,key):
        def read():
            with connect() as c:
                self.owner(c)
                rows=c.execute('SELECT entry_json FROM sdk_session_entries WHERE conversation_id=? AND project_key=? AND session_id=? AND subpath=? ORDER BY seq',(self.cid,key.get('project_key',''),key['session_id'],key.get('subpath') or '')).fetchall()
            return [json.loads(r['entry_json']) for r in rows] if rows else None
        return await asyncio.to_thread(read)

    async def list_subkeys(self,key):
        def read():
            with connect() as c:
                self.owner(c)
                return [r['subpath'] for r in c.execute("SELECT DISTINCT subpath FROM sdk_session_entries WHERE conversation_id=? AND project_key=? AND session_id=? AND subpath<>''",(self.cid,key.get('project_key',''),key['session_id'])).fetchall()]
        return await asyncio.to_thread(read)

    async def delete(self,key):
        def remove():
            with connect() as c:
                begin_write(c);self.owner(c)
                if key.get('subpath'):
                    c.execute('DELETE FROM sdk_session_entries WHERE conversation_id=? AND project_key=? AND session_id=? AND subpath=?',(self.cid,key.get('project_key',''),key['session_id'],key['subpath']))
                else:c.execute('DELETE FROM sdk_session_entries WHERE conversation_id=? AND project_key=? AND session_id=?',(self.cid,key.get('project_key',''),key['session_id']))
        await asyncio.to_thread(remove)
