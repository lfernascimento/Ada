import {characterImage,characterAccent} from './characters';
import type {CSSProperties} from 'react';
import {useState,useEffect} from 'react';
import type {Spec} from './App';
import type {Character} from './CharacterStart';
type Identity={name:string;avatar:Character;color:string};
export default function AgentBrief({identity,onBack,onPrepared}:{identity:Identity;onBack:()=>void;onPrepared:(spec:Spec,rationale:string)=>void}){
 useEffect(()=>{window.scrollTo(0,0);},[]);
 const [goal,setGoal]=useState(''),[guidance,setGuidance]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const image=characterImage(identity.avatar);
 async function prepare(){setBusy(true);setError('');try{
  const response=await fetch('/api/create/messages',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({identity,direct:true,messages:[{role:'user',content:goal+(guidance.trim()?'\n\nOrientações e contexto: '+guidance:'')}]})});
  const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Não foi possível preparar o agente.');
  if(result.type!=='proposal'||!result.spec)throw new Error('A Ada não concluiu a proposta. Tente novamente.');
  onPrepared({...result.spec,name:identity.name,style:{...result.spec.style,avatar:identity.avatar,color:identity.color}},result.rationale||'');
 }catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <main className="setup-page" data-character={identity.avatar} style={{'--agent-accent':characterAccent(identity.avatar),'--creation-step':'66%'} as CSSProperties}><header className="setup-header"><div className="setup-brand"><img src="/ada-marca-girassois.svg" alt=""/><span>Ada</span></div><div className="setup-progress">NOVO AGENTE · 2 DE 3</div><button className="setup-back" disabled={busy} onClick={onBack}>← Identidade</button></header><div className="setup-layout"><section className="setup-preview"><div className="setup-glow"/><img className="setup-hero" src={image} alt="Personagem escolhido"/><div className="setup-preview-caption"><span className="setup-kicker">SEU NOVO AGENTE</span><h1>{identity.name}</h1></div></section><form className="setup-content" onSubmit={e=>{e.preventDefault();if(goal.trim()&&!busy)prepare()}}><div className="setup-kicker">02 / PROPÓSITO</div><h2>O que vamos<br/><em>fazer juntos?</em></h2><p className="setup-lead">Conte o trabalho que {identity.name} vai ajudar a realizar. Você poderá ajustar e evoluir o agente depois.</p><label className="setup-label" htmlFor="agent-goal">Objetivo</label><textarea id="agent-goal" className="setup-input setup-rules" required maxLength={8000} value={goal} onChange={e=>setGoal(e.target.value)} placeholder="Descreva um problema que deseja resolver ou um resultado que deseja alcançar."/><label className="setup-label" htmlFor="agent-guidance">Contexto e preferências · opcional</label><textarea id="agent-guidance" className="setup-input setup-rules" maxLength={3000} value={guidance} onChange={e=>setGuidance(e.target.value)} placeholder="Escreva livremente o que seria útil saber."/>{error&&<p className="setup-error" role="alert">{error}</p>}<div className="setup-buttons"><button className="setup-primary" disabled={busy||!goal.trim()}>{busy?'Preparando…':'Preparar agente'} <span>→</span></button></div></form></div></main>
}
