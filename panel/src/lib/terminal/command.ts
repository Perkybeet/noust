/**
 * A command typed in one line, turned into the argument list the server runs.
 *
 * The server never gives a command to a shell: it is a program and its arguments, as given.
 * So this does only what makes typing one line bearable and nothing a shell does besides:
 * words are separated by spaces, a quote (`'` or `"`) keeps spaces together, a backslash
 * keeps the next character as it is. There is no expansion of `$VAR`, `*` or `~`, and no pipe,
 * redirection or `&&`: those are ordinary characters, and the page says so when it sees one, so
 * nobody believes `cmd | grep x` is going to be filtered.
 */

export interface ParsedCommand {
  /** The program and its arguments. */
  argv: string[];
  /** The quote that was opened and never closed, when the line ends inside one. */
  unclosed: "'" | '"' | null;
}

/**
 * Splits one line of text into arguments.
 *
 * Inside single quotes everything is literal. Inside double quotes a backslash keeps a double
 * quote or a backslash; elsewhere it is itself. Outside quotes a backslash keeps the next
 * character, so `a\ b` is one argument. An empty pair of quotes is an empty argument.
 */
export function parseCommand(line: string): ParsedCommand {
  const argv: string[] = [];
  let current = "";
  let started = false;
  let quote: "'" | '"' | null = null;

  for (let index = 0; index < line.length; index++) {
    const char = line.charAt(index);
    if (quote === "'") {
      if (char === "'") quote = null;
      else current += char;
    } else if (quote === '"') {
      if (char === '"') {
        quote = null;
      } else if (char === "\\" && (line.charAt(index + 1) === '"' || line.charAt(index + 1) === "\\")) {
        current += line.charAt(index + 1);
        index++;
      } else {
        current += char;
      }
    } else if (char === "'" || char === '"') {
      quote = char;
      started = true;
    } else if (char === "\\" && index + 1 < line.length) {
      current += line.charAt(index + 1);
      started = true;
      index++;
    } else if (/\s/.test(char)) {
      if (started || current !== "") argv.push(current);
      current = "";
      started = false;
    } else {
      current += char;
      started = true;
    }
  }
  if (started || current !== "") argv.push(current);
  return { argv, unclosed: quote };
}

/** The characters a shell would act on that this does not: the page tells the person. */
const SHELL_SYNTAX = /[|&;<>$`*?~]|\(|\)/;

/**
 * The arguments that hold something a shell would have interpreted, so the page can say it
 * is passed as it is.
 */
export function shellSyntaxIn(argv: readonly string[]): string[] {
  return argv.filter((argument) => SHELL_SYNTAX.test(argument));
}

/** Words that need no quoting to be typed into a shell as they are. */
const PLAIN_WORD = /^[A-Za-z0-9_@%+=:,./-]+$/;

/**
 * One argument written as a shell needs it, for the command-line equivalent of what was typed:
 * the page shows `noust app exec shop.example.com -- rails runner 'puts 1'` to be copied.
 */
export function quoteForShell(argument: string): string {
  if (PLAIN_WORD.test(argument)) return argument;
  return `'${argument.replaceAll("'", "'\\''")}'`;
}

/** An argument list as one line a shell reads back into the same list. */
export function commandLine(argv: readonly string[]): string {
  return argv.map(quoteForShell).join(" ");
}
