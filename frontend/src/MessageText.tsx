import {Fragment} from 'react';
function Inline({text}:{text:string}){return <>{text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\((?:https?:\/\/|\/api\/)[^\s)]+\)|https?:\/\/[^\s<>]+)/g).map((part,i)=>{
 if(part.startsWith('**')&&part.endsWith('**'))return <strong key={i}>{part.slice(2,-2)}</strong>;
 if(part.startsWith('`')&&part.endsWith('`'))return <code key={i}>{part.slice(1,-1)}</code>;
 const link=part.match(/^\[([^\]]+)\]\(((?:https?:\/\/|\/api\/)[^\s)]+)\)$/);if(link)return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer">{link[1]}</a>;
 if(/^https?:\/\//.test(part))return <a key={i} href={part} target="_blank" rel="noopener noreferrer">{part}</a>;
 return <Fragment key={i}>{part}</Fragment>;
 })}</>}
export default function MessageText({content}:{content:string}){
 return <div className="message-markdown">{content.split(/(```[\s\S]*?```)/g).map((block,i)=>{
 if(block.startsWith('```')){const lines=block.slice(3,-3).split('\n');return <pre key={i}><code>{lines.slice(1).join('\n')}</code></pre>}
 return <Fragment key={i}>{block.split(/\n\s*\n/).filter(Boolean).map((paragraph,j)=>{
 const lines=paragraph.split('\n');if(lines.every(line=>/^\s*[-*] /.test(line)))return <ul key={j}>{lines.map((line,k)=><li key={k}><Inline text={line.replace(/^\s*[-*] /,'')}/></li>)}</ul>;
 if(lines.every(line=>/^\s*\d+[.)] /.test(line)))return <ol key={j}>{lines.map((line,k)=><li key={k}><Inline text={line.replace(/^\s*\d+[.)] /,'')}/></li>)}</ol>;
 return <p key={j}>{lines.map((line,k)=><Fragment key={k}>{k>0&&<br/>}{/^#{1,6} /.test(line)?<strong><Inline text={line.replace(/^#{1,6} /,'')}/></strong>:<Inline text={line}/>}</Fragment>)}</p>;
 })}</Fragment>;
 })}</div>
}
