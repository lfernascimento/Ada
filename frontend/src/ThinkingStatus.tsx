import {useEffect,useRef,useState} from 'react';
function duration(seconds:number){return seconds<60?`${seconds}s`:`${Math.floor(seconds/60)}min ${seconds%60}s`}
export default function ThinkingStatus({active,failed,name}:{active:boolean;failed:boolean;name:string}){
 const began=useRef<number|null>(null);const [seconds,setSeconds]=useState(0),[finished,setFinished]=useState(false);
 useEffect(()=>{if(!active){if(began.current!==null){setSeconds(Math.max(1,Math.round((Date.now()-began.current)/1000)));setFinished(true)}return}began.current=Date.now();setSeconds(0);setFinished(false);const timer=window.setInterval(()=>setSeconds(Math.floor((Date.now()-began.current!)/1000)),1000);return()=>window.clearInterval(timer)},[active]);
 if(!active&&!finished)return null;
 return <div className={'thinking-status '+(active?'is-active':'')} role="status" aria-live="polite">{active?<><span className="thinking-dots" aria-hidden="true"><i/><i/><i/></span><span>Preparando resposta de {name}</span><small aria-hidden="true">{duration(seconds)}</small></>:<span>{failed?'Tentativa interrompida':'Resposta concluída'} · {duration(seconds)}</span>}</div>
}
