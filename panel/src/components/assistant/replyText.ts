/**
 * The text of an answer, read into a short list of blocks and a handful of marks. The model's words come from outside and the
 * console's policy forbids a string-to-DOM sink, so nothing here ever makes HTML: what comes out is data that `MessageText` draws
 * as elements, and everything this reader does not know (a tag, a script, a table, an image) stays plain text, which React
 * escapes. There is no image: a `![x](y)` is a sentence that says an image was not shown.
 */

/** A piece of a line: text, bold, code or a link whose destination is judged here. */
export type Inline =
  | { k: "text"; text: string }
  | { k: "strong"; text: string }
  | { k: "code"; text: string }
  /** A destination that is not a web address: drawn as its label, with the destination in view and no link. */
  | { k: "dead"; label: string; destination: string }
  | { k: "link"; label: string; href: string; host: string }
  | { k: "image"; alt: string };

export type ReplyBlock = { k: "p"; inline: Inline[] } | { k: "list"; ordered: boolean; items: Inline[][] } | { k: "code"; text: string };

/** How much of a reply is drawn: the server bounds it already, and a page of ten thousand paragraphs is not a conversation. */
export const MAX_BLOCKS = 200;

// A destination may hold one level of parentheses (`javascript:alert(1)`, a page whose name has them): the pair is part of it.
const DESTINATION = String.raw`((?:[^()\s]|\([^()\s]*\))*)`;
const MARK = new RegExp(String.raw`!\[([^\]]*)\]\(${DESTINATION}\)|\[([^\]]+)\]\(${DESTINATION}\)|`+"`([^`]+)`"+String.raw`|\*\*([^*]+)\*\*|__([^_]+)__`, "g");

/**
 * Judges a destination. Only `http:` and `https:` are ever links; one with credentials in it is the classic way to make an
 * address read as another host, so it is not a link either. The host is what the person sees beside the link, always.
 *
 * @param destination - What the answer put between the parentheses.
 * @returns The address and its host, or null when it must not be a link.
 */
export function judgeDestination(destination: string): { href: string; host: string } | null {
  let url: URL;
  try {
    url = new URL(destination);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  if (url.username !== "" || url.password !== "") return null;
  if (url.host === "") return null;
  return { href: url.href, host: url.host };
}

/**
 * The marks of one line.
 *
 * @param text - The text of a paragraph or an item.
 * @returns Its pieces, in order.
 */
export function readInline(text: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const match of text.matchAll(MARK)) {
    const at = match.index;
    if (at > last) out.push({ k: "text", text: text.slice(last, at) });
    const [, imageAlt, , label, destination, code, strongA, strongB] = match;
    if (imageAlt !== undefined) {
      out.push({ k: "image", alt: imageAlt });
    } else if (label !== undefined && destination !== undefined) {
      const judged = judgeDestination(destination);
      out.push(judged === null ? { k: "dead", label, destination } : { k: "link", label, href: judged.href, host: judged.host });
    } else if (code !== undefined) {
      out.push({ k: "code", text: code });
    } else {
      out.push({ k: "strong", text: strongA ?? strongB ?? "" });
    }
    last = at + match[0].length;
  }
  if (last < text.length) out.push({ k: "text", text: text.slice(last) });
  return out;
}

const BULLET = /^\s{0,3}[-*+]\s+(.*)$/;
const NUMBERED = /^\s{0,3}\d{1,3}[.)]\s+(.*)$/;
const FENCE = /^\s{0,3}```/;
const HEADING = /^\s{0,3}#{1,6}\s+(.*)$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;

/**
 * An answer as blocks: paragraphs, lists, fenced code. A heading becomes a paragraph in bold, a quote and a rule are dropped
 * to their text (or nothing), and anything else is a paragraph.
 *
 * @param source - The text of the answer, Markdown as a model writes it.
 * @returns The blocks, at most {@link MAX_BLOCKS}.
 */
export function readReply(source: string): ReplyBlock[] {
  const lines = source.replaceAll("\r\n", "\n").split("\n");
  const blocks: ReplyBlock[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = (): void => {
    if (paragraph.length > 0) blocks.push({ k: "p", inline: readInline(paragraph.join(" ")) });
    paragraph = [];
  };
  const flushList = (): void => {
    if (list !== null) blocks.push({ k: "list", ordered: list.ordered, items: list.items.map(readInline) });
    list = null;
  };

  for (let index = 0; index < lines.length && blocks.length < MAX_BLOCKS; index += 1) {
    const line = lines[index] ?? "";
    if (FENCE.test(line)) {
      flushParagraph();
      flushList();
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !FENCE.test(lines[index] ?? "")) {
        code.push(lines[index] ?? "");
        index += 1;
      }
      blocks.push({ k: "code", text: code.join("\n") });
      continue;
    }
    if (line.trim() === "" || RULE.test(line)) {
      flushParagraph();
      flushList();
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = NUMBERED.exec(line);
    if (bullet !== null || numbered !== null) {
      flushParagraph();
      const ordered = numbered !== null && bullet === null;
      const text = (bullet ?? numbered)?.[1] ?? "";
      if (list !== null && list.ordered !== ordered) flushList();
      list ??= { ordered, items: [] };
      list.items.push(text);
      continue;
    }
    if (list !== null && /^\s{2,}\S/.test(line)) {
      // The rest of an item written over two lines.
      const items = list.items;
      items[items.length - 1] = `${items[items.length - 1] ?? ""} ${line.trim()}`;
      continue;
    }
    flushList();
    const heading = HEADING.exec(line);
    if (heading !== null) {
      flushParagraph();
      blocks.push({ k: "p", inline: [{ k: "strong", text: (heading[1] ?? "").replaceAll(/[*_`]/g, "") }] });
      continue;
    }
    paragraph.push(line.replace(/^\s{0,3}>\s?/, "").trim());
  }
  flushParagraph();
  flushList();
  return blocks;
}
