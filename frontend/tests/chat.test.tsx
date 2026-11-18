import {test} from 'node:test';
import assert from 'node:assert/strict';
import {render,screen,fireEvent,waitFor,act,cleanup} from '@testing-library/react';
import App from '../src/App';
const spec={name:'Teste',purpose:'Ajudar em tarefas.',instructions:[],tools:[],autonomy_policy:{},resources:{},limits:{max_steps:4},style:{color:'amber',avatar:'flower'}};
function response(data:unknown,ok=true){return {ok,json:async()=>data} as Response}
async function openAgent(){await act(async()=>{render(<App/>)});fireEvent.click(screen.getByRole('button',{name:'← Voltar',exact:true}));await screen.findByRole('heading',{name:'Teste',level:1})}
function setup(){let complete:(r:Response)=>void=()=>{};let calls=0;
 globalThis.fetch=async(path:any,options:any={})=>{const url=String(path);if(url.endsWith('/messages')&&options.method==='POST'){calls++;return new Promise<Response>(resolve=>{complete=resolve})}
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
