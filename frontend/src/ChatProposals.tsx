import {useEffect,useState} from 'react';
type Proposal={id:string;repository:string;title:string;description:string;diff:string;validation:{status:string;message:string};can_push:boolean;status:string;url?:string};
export default function ChatProposals({agentId,cid,refreshKey}:{agentId:number;cid:number;refreshKey:number}){
 const [items,setItems]=useState<Proposal[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState('');
 async function load(){const r=await fetch(`/api/agents/${agentId}/conversations/${cid}/code-proposals`);if(r.ok)setItems(await r.json())}
 useEffect(()=>{load()},[agentId,cid,refreshKey]);
 async function publish(id:string){setBusy(id);setError('');try{const r=await fetch(`/api/repository/drafts/${id}/publish`,{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Não foi possível criar o PR.');await load()}catch(e){setError((e as Error).message)}finally{setBusy('')}}
 return <>{items.map(p=><section className="panel-card" key={p.id}><h3>{p.title}</h3><p>{p.repository}</p><p>{p.description}</p><details><summary>Ver alterações · opcional</summary><pre style={{whiteSpace:'pre-wrap',maxHeight:300,overflow:'auto'}}>{p.diff}</pre></details>{p.url?<a href={p.url} target="_blank" rel="noopener noreferrer">Revisar PR no GitHub</a>:<button className="primary" disabled={!!busy||!p.can_push||p.validation.status==='failed'||p.status!=='ready'} onClick={()=>publish(p.id)}>Autorizar criação do PR</button>}{!p.can_push&&<p>Autorize escrita nesse repositório no GitHub antes de criar o PR.</p>}</section>)}{error&&<p className="error" role="alert">{error}</p>}</>
}
