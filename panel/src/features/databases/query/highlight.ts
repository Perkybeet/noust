/**
 * A statement split into what the editor draws differently: keywords, strings, quoted
 * identifiers, numbers, comments and the rest. Lexical only, never a parser: it only has to
 * agree with itself about where a string or a comment ends, so a quote typed half-way does not
 * repaint the whole editor wrongly for more than the line being typed.
 */

export type TokenKind = "keyword" | "string" | "identifier" | "number" | "comment" | "punctuation" | "text";

export interface Token {
  kind: TokenKind;
  text: string;
}

/** The words drawn as keywords, across PostgreSQL and MySQL/MariaDB. */
const KEYWORDS = new Set(
  `select from where and or not in is null like ilike between exists as on join left right inner outer full cross natural
  using group by order having limit offset fetch first next rows only distinct all any some union intersect except case when
  then else end insert into values update set delete returning create alter drop table view index sequence schema database
  if cascade restrict primary key foreign references unique check default constraint with recursive explain analyze begin
  commit rollback grant revoke to true false asc desc nulls last over partition window filter lateral show describe
  truncate vacuum copy do language function procedure trigger returns cast interval current_date current_timestamp now
  count sum avg min max coalesce nullif greatest least extract date time timestamp boolean integer bigint text varchar char
  numeric json jsonb uuid serial`
    .split(/\s+/)
    .filter((word) => word !== ""),
);

const PATTERNS: readonly [TokenKind, RegExp][] = [
  ["comment", /^--[^\n]*/],
  ["comment", /^\/\*[\s\S]*?(?:\*\/|$)/],
  ["string", /^(?:[EeBbXx])?'(?:[^'\\]|\\.|'')*(?:'|$)/],
  ["string", /^\$([A-Za-z_]*)\$[\s\S]*?(?:\$\1\$|$)/],
  ["identifier", /^"(?:[^"]|"")*(?:"|$)/],
  ["identifier", /^`(?:[^`]|``)*(?:`|$)/],
  ["number", /^(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+)/],
  ["text", /^[A-Za-z_][A-Za-z0-9_$]*/],
  ["text", /^\s+/],
  ["punctuation", /^[(),;.*=<>!+\-/%:[\]{}|&^~@#?]+/],
];

export function tokenize(source: string): Token[] {
  const tokens: Token[] = [];
  let rest = source;
  while (rest.length > 0) {
    let matched = false;
    for (const [kind, pattern] of PATTERNS) {
      const found = pattern.exec(rest);
      const text = found?.[0];
      if (text === undefined || text === "") continue;
      const resolved: TokenKind = kind === "text" && KEYWORDS.has(text.toLowerCase()) ? "keyword" : kind;
      const previous = tokens[tokens.length - 1];
      if (previous?.kind === resolved && (resolved === "text" || resolved === "punctuation")) previous.text += text;
      else tokens.push({ kind: resolved, text });
      rest = rest.slice(text.length);
      matched = true;
      break;
    }
    if (!matched) {
      const previous = tokens[tokens.length - 1];
      const character = rest.charAt(0);
      if (previous?.kind === "text") previous.text += character;
      else tokens.push({ kind: "text", text: character });
      rest = rest.slice(1);
    }
  }
  return tokens;
}
