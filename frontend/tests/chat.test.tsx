import {test} from 'node:test';
import assert from 'node:assert/strict';
import {render,screen,fireEvent,waitFor,act,cleanup} from '@testing-library/react';
import App from '../src/App';
import RuntimeControls from '../src/RuntimeControls';
import Workflows from '../src/Workflows';
const spec={name:'Teste',purpose:'Ajudar em tarefas.',instructions:[],tools:[],autonomy_policy:{},resources:{},limits:{max_steps:4},style:{color:'amber',avatar:'flower'}};
function response(data:unknown,ok=true){return {ok,json:async()=>data} as Response}
async function openAgent(){await act(async()=>{render(<App/>)});fireEvent.click(screen.getByRole('button',{name:'← Voltar',exact:true}));await screen.findByRole('heading',{name:'Teste',level:1})}
function setup(){let complete:(r:Response)=>void=()=>{};let calls=0;
 globalThis.fetch=async(path:any,options:any={})=>{const url=String(path);if(url.endsWith('/messages/stream')&&options.method==='POST'){calls++;return new Promise<Response>(resolve=>{complete=resolve})}
 if(url==='/api/agents')return response([{id:1,name:'Teste',avatar:'flower',color:'amber',active_version:1}]);
 if(url==='/api/agents/1')return response({id:1,active_version:1,spec});
 if(url.endsWith('/conversations')&&options.method==='POST')return response({id:1});
 return response([])};
 return {finish:(ok=true)=>complete(response(ok?{text:'Resposta pronta.'}:{detail:'Falha simulada.'},ok)),calls:()=>calls};
}
test('mensagem aparece antes da resposta e mostra atividade',async()=>{const mock=setup();try{
 await openAgent();fireEvent.change(screen.getByLabelText('Sua mensagem'),{target:{value:'Minha tarefa'}});fireEvent.click(screen.getByRole('button',{name:'Enviar mensagem'}));
 await waitFor(()=>assert.equal(mock.calls(),1));assert.ok(screen.getByText('Minha tarefa'));assert.ok(screen.getByText('Preparando resposta de Teste'));assert.equal((screen.getByLabelText('Sua mensagem') as HTMLTextAreaElement).value,'');assert.equal(screen.queryByText('Resposta pronta.'),null);
 await act(async()=>mock.finish());await screen.findByText('Resposta pronta.');assert.equal(screen.getAllByText('Minha tarefa').length,1);assert.equal(screen.queryByText('Preparando resposta de Teste'),null);
 }finally{cleanup()}});
test('falha preserva o texto e reenvio não duplica a mensagem otimista',async()=>{const mock=setup();try{
 await openAgent();fireEvent.change(screen.getByLabelText('Sua mensagem'),{target:{value:'Tarefa para reenviar'}});fireEvent.click(screen.getByRole('button',{name:'Enviar mensagem'}));await waitFor(()=>assert.equal(mock.calls(),1));await act(async()=>mock.finish(false));await screen.findByText('Falha simulada.');
 assert.equal((screen.getByLabelText('Sua mensagem') as HTMLTextAreaElement).value,'Tarefa para reenviar');assert.ok(screen.getByText(/Não enviada/));
 fireEvent.click(screen.getByRole('button',{name:'Enviar mensagem'}));await waitFor(()=>assert.equal(mock.calls(),2));assert.equal(screen.getAllByText('Tarefa para reenviar').length,1);
 await act(async()=>mock.finish());await screen.findByText('Resposta pronta.');assert.equal(screen.getAllByText('Tarefa para reenviar').length,1);
 }finally{cleanup()}});

test('texto chega em partes e o botão interrompe a execução',async()=>{setup();const original=globalThis.fetch;let controller:ReadableStreamDefaultController<Uint8Array>|undefined;let stopped=false;
 globalThis.fetch=async(path:any,options:any={})=>{const url=String(path);if(url.endsWith('/messages/stream'))return {ok:true,body:new ReadableStream<Uint8Array>({start(c){controller=c}})} as Response;
 if(url==='/api/runs/9/cancel'){stopped=true;controller!.enqueue(new TextEncoder().encode(JSON.stringify({type:'cancelled'})+'\n'));controller!.close();return response({status:'cancelled'})}
 return original(path,options)};
 try{await openAgent();fireEvent.change(screen.getByLabelText('Sua mensagem'),{target:{value:'Tarefa longa'}});fireEvent.click(screen.getByRole('button',{name:'Enviar mensagem'}));await waitFor(()=>assert.ok(controller));
 await act(async()=>controller!.enqueue(new TextEncoder().encode('{"type":"started","run_id":9}\n{"type":"text_delta","text":"Análise em andamento"}\n')));
 await screen.findByText('Análise em andamento');fireEvent.click(screen.getByRole('button',{name:'Interromper tarefa'}));await screen.findByText(/Execução interrompida/);assert.ok(stopped);assert.equal((screen.getByLabelText('Sua mensagem') as HTMLTextAreaElement).value,'Tarefa longa');
 }finally{cleanup()}});

test('aprovação da sandbox exige uma escolha explícita no chat',async()=>{let decision:unknown;globalThis.fetch=async(path:any,options:any={})=>{
 if(options.method==='POST'){decision=JSON.parse(options.body);return response({accepted:true})}
 return response({run_id:9,requests:[{id:'approval',kind:'approval',question:'Executar código na sandbox?',detail:'print(1)'}]})};
 try{render(<RuntimeControls agentId={1} cid={2} busy={false} onRun={()=>{}}/>);await screen.findByText('Executar código na sandbox?');assert.equal(decision,undefined);fireEvent.click(screen.getByRole('button',{name:'Recusar'}));await waitFor(()=>assert.deepEqual(decision,{approved:false}));assert.equal(screen.queryByText('Executar código na sandbox?'),null)}finally{cleanup()}});

test('workflow permite pausar e aprovar o resultado',async()=>{let decision:any,runUrl='';const flow={id:7,name:'Pesquisa',objective:'Analisar dados',active_version:1,enabled:0,interval_hours:24,max_daily_runs:1,status:'idle'};
 globalThis.fetch=async(path:any,options:any={})=>{const url=String(path);if(url.endsWith('/run?review=true')){runUrl=url;return response({id:4,status:'waiting_review',text:'Entrega preparada',conversation_id:3,version:1})}if(url.endsWith('/runs/4/review')){decision=JSON.parse(options.body);return response({status:'completed'})}return response([flow])};
 try{render(<Workflows agentId={1} refreshKey={0} onOpen={()=>{}}/>);await screen.findByText('Pesquisa · v1');fireEvent.click(screen.getByLabelText('Pausar esta execução manual para revisão'));fireEvent.click(screen.getByRole('button',{name:'Executar · usa API'}));await screen.findByText(/Execução 4 · Aguardando revisão/);assert.ok(runUrl.endsWith('review=true'));fireEvent.click(screen.getByText(/Execução 4 · Aguardando revisão/));fireEvent.click(screen.getByRole('button',{name:'Aprovar resultado'}));await waitFor(()=>assert.deepEqual(decision,{approved:true}));await screen.findByText(/Execução 4 · Concluído/)}finally{cleanup()}});
