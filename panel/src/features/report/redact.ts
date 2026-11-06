/**
 * Hides what looks like a secret in the text of the browser, before it goes in a report.
 *
 * The support report comes masked from the server. The block of the browser (the page, the last
 * errors, the words the person wrote when an error screen opened the report) does not pass through
 * the server, and the report is also made when the server cannot be reached, which is when an
 * error screen appears. So the browser masks it itself, with the shapes of `mask_secret_shapes`:
 * not a list of its own but `api/secretShapes.gen.ts`, which `scripts/generate_secret_shapes.py`
 * writes from the tuple the server walks, and the table of cases both sides are held to.
 */

import { SECRET_MASK, SECRET_SHAPES } from "../../api/secretShapes.gen";

const COMPILED = SECRET_SHAPES.map((shape) => ({ keepHead: shape.keepHead, pattern: new RegExp(shape.source, shape.flags) }));

export interface Redacted {
  /** The text with a mask where a secret was. */
  text: string;
  /** How many secrets were hidden. */
  holes: number;
}

/**
 * Masks every shape of a secret in a text, in the order the server does.
 *
 * @param text - Text that may hold a credential.
 * @returns The text with each credential replaced by `***` (the head of an assignment kept, so
 * `password=***` still says what was hidden), and how many were.
 */
export function redact(text: string): Redacted {
  let holes = 0;
  let masked = text;
  for (const { keepHead, pattern } of COMPILED) {
    masked = masked.replace(pattern, (match: string, head: unknown) => {
      const replacement = keepHead && typeof head === "string" ? `${head}${SECRET_MASK}` : SECRET_MASK;
      // A mask a pass already wrote (the password of postgres://a:***@h) is found again and is not a second secret.
      if (replacement !== match) holes += 1;
      return replacement;
    });
  }
  return { text: masked, holes };
}

/** A piece of a text: what was written, or what was hidden. */
export interface Piece {
  hidden: boolean;
  text: string;
}

/**
 * Splits a text where a secret was hidden, so a preview can mark each hole as hidden. It reads the
 * mask itself, so it marks what the server hid in the support report as well as what this module did.
 *
 * @param text - A text that may hold masks.
 * @returns The pieces in order, hidden ones in the place of each mask.
 */
export function pieces(text: string): Piece[] {
  const out: Piece[] = [];
  text.split(SECRET_MASK).forEach((part, index) => {
    if (index > 0) out.push({ hidden: true, text: SECRET_MASK });
    if (part !== "") out.push({ hidden: false, text: part });
  });
  return out;
}
