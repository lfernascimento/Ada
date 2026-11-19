type Interaction={role:'user'|'assistant';content:string};
export type RuntimeEvent={type:string;text?:string;detail?:string;run_id?:number;payload?:Record<string,unknown>;interactions?:Interaction[]};
export async function chatStream(path:string,body:unknown,onEvent:(event:RuntimeEvent)=>void):Promise<{text:string;interactions?:Interaction[]}>{
 const response=await fetch('/api'+path+'/messages/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 if(!response.ok){let data;try{data=await response.json()}catch{}throw new Error(typeof data?.detail==='string'?data.detail:'Não foi possível conectar à Ada.')}
 // Supports transports that return an ordinary JSON result, including embedded clients.
 if(!response.body){const data=await response.json();if(typeof data.text!=='string')throw new Error('Resposta inválida.');return data}
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',answer:string|undefined,interactions:Interaction[]|undefined;
 const consume=(line:string)=>{if(!line.trim())return;const event:RuntimeEvent=JSON.parse(line);onEvent(event);if(event.type==='result'){answer=event.text;interactions=event.interactions}if(event.type==='error')throw new Error(event.detail||'A execução falhou.');if(event.type==='cancelled')throw new Error('Execução interrompida. O consumo já realizado pode ser cobrado.')};
 try{while(true){const {done,value}=await reader.read();buffer+=decoder.decode(value,{stream:!done});if(buffer.length>2_000_000)throw new Error('A resposta excedeu o limite de transmissão.');let at;while((at=buffer.indexOf('\n'))>=0){consume(buffer.slice(0,at));buffer=buffer.slice(at+1)}if(done)break}consume(buffer);if(answer===undefined)throw new Error('A conexão terminou antes da confirmação da resposta.');return {text:answer,interactions}}
 finally{await reader.cancel().catch(()=>{});reader.releaseLock()}
}
