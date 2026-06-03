export function compactCard(title: string, lines: string[]): string {
  return [`## ${title}`, ...lines.filter(Boolean)].join('\n');
}
