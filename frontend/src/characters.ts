export type Character='flower'|'mouse'|'toucan'|'hedgehog'|'bear'|'crab';
export const characters:{id:Character;name:string;image:string;description:string}[]=[
 {id:'flower',name:'Flor',image:'/ada-flor.png',description:'Uma ideia começando a florescer.'},
 {id:'mouse',name:'Ratinho',image:'/avatar-ratinho.png',description:'Curioso e atento aos detalhes.'},
 {id:'toucan',name:'Tucaninho',image:'/avatar-tucano-v2.png',description:'Expressivo e cheio de presença.'},
 {id:'hedgehog',name:'Ouriço',image:'/avatar-ourico.png',description:'Atento e cheio de curiosidade.'},
 {id:'bear',name:'Ursinho inspirado em Porgy',image:'/avatar-porgy.png',description:'Um companheiro para suas descobertas.'},
 {id:'crab',name:'Caranguejo do Manguebeat',image:'/avatar-caranguejo.png',description:'Do mangue, novas ideias para o mundo.'},
];
export function characterImage(id?:string){return characters.find(character=>character.id===id)?.image||characters[0].image;}

const accents:Record<string,string>={flower:'#896500',mouse:'#715b80',toucan:'#49675d',hedgehog:'#785e49',bear:'#4d6379',crab:'#984b37'};
export function characterAccent(id?:string){return accents[id||'']||'#72776f';}
