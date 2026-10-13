/**
 * Words of an argument line, split the way a shell would split them and never run by one: a
 * quoted stretch is one word (`--build-arg 'NOTE=two words'`), a backslash keeps the next
 * character, and nothing else is special. The server checks every word against its closed list.
 */
export function splitWords(line: string): string[] {
  const words: string[] = [];
  let current = "";
  let started = false;
  let quote: "'" | '"' | null = null;
  for (let index = 0; index < line.length; index += 1) {
    const char = line.charAt(index);
    if (quote !== null) {
      if (char === quote) quote = null;
      else if (char === "\\" && quote === '"' && index + 1 < line.length) current += line.charAt(++index);
      else current += char;
    } else if (char === "'" || char === '"') {
      quote = char;
      started = true;
    } else if (char === "\\" && index + 1 < line.length) {
      current += line.charAt(++index);
      started = true;
    } else if (/\s/.test(char)) {
      if (started) words.push(current);
      current = "";
      started = false;
    } else {
      current += char;
      started = true;
    }
  }
  if (started) words.push(current);
  return words;
}

/** Words joined back into a line a person can edit: a word with a space or a quote is quoted. */
export function joinWords(words: readonly string[]): string {
  return words.map((word) => (/[\s'"\\]|^$/.test(word) ? `'${word.replace(/'/g, "'\\''")}'` : word)).join(" ");
}

/** Names separated by commas, spaces or new lines. */
export function splitNames(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((name) => name.trim())
    .filter((name) => name !== "");
}
