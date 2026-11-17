// Nomes selecionados para novos agentes.
export const agentNames = [
  'Íris', 'Otto', 'Nina', 'Luna', 'Alma', 'Leo', 'Theo', 'Tom', 'Nora', 'Juno',
  'Pip', 'Ben', 'Bowie', 'Ziggy', 'Agnès', 'Wes', 'Akira', 'Hayao', 'Charlie', 'Buster',
] as const;

export function chooseAgentName(existingNames: string[]): string {
  const normalize = (name: string) => name.trim().normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR');
  const used = new Set(existingNames.map(normalize));
  const available = agentNames.filter(name => !used.has(normalize(name)));
  const choices: readonly string[] = available.length ? available : agentNames;
  return choices[Math.floor(Math.random() * choices.length)];
}
