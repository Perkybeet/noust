/**
 * Keys to the bytes a terminal program expects.
 *
 * The mapping is xterm's, because that is what `TERM=xterm-256color` promises: an arrow is
 * `ESC [ A`, or `ESC O A` once the program has asked for application cursor keys; Home and End
 * likewise; the function keys have their own codes; a modifier is the number
 * `1 + shift + 2 alt + 4 ctrl + 8 meta` put into the sequence (`ESC [ 1 ; 5 C` is Ctrl+Right);
 * Ctrl+letter is the control character; Alt+key is the key with an escape in front of it.
 *
 * Ctrl+Shift+C and Ctrl+Shift+V are not keys of the program: they are the console's copy and
 * paste, as in every terminal on Linux, because Ctrl+C has to stay an interrupt. Nothing here
 * sends them.
 */

/** The part of a keyboard event the mapping reads. */
export interface KeyInput {
  key: string;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  metaKey: boolean;
}

/** What the program has switched on that changes what keys send. */
export interface KeyModes {
  /** DECCKM: arrows, Home and End send `ESC O` forms. */
  applicationCursor: boolean;
}

/** The modifier number of a sequence, or 1 for none. */
function modifierNumber(input: KeyInput): number {
  return 1 + (input.shiftKey ? 1 : 0) + (input.altKey ? 2 : 0) + (input.ctrlKey ? 4 : 0) + (input.metaKey ? 8 : 0);
}

/** `ESC [ A`, `ESC [ 1 ; 5 A` or (application mode, no modifier) `ESC O A`. */
function cursorKey(final: string, input: KeyInput, modes: KeyModes): string {
  const modifier = modifierNumber(input);
  if (modifier > 1) return `\x1b[1;${modifier}${final}`;
  return modes.applicationCursor ? `\x1bO${final}` : `\x1b[${final}`;
}

/** `ESC [ 5 ~` or `ESC [ 5 ; 5 ~`. */
function tildeKey(code: number, input: KeyInput): string {
  const modifier = modifierNumber(input);
  return modifier > 1 ? `\x1b[${code};${modifier}~` : `\x1b[${code}~`;
}

/** F1 to F4: `ESC O P` and its modified `ESC [ 1 ; 2 P`. */
function lowFunctionKey(final: string, input: KeyInput): string {
  const modifier = modifierNumber(input);
  return modifier > 1 ? `\x1b[1;${modifier}${final}` : `\x1bO${final}`;
}

const TILDE_CODES: Readonly<Record<string, number>> = {
  Insert: 2,
  Delete: 3,
  PageUp: 5,
  PageDown: 6,
  F5: 15,
  F6: 17,
  F7: 18,
  F8: 19,
  F9: 20,
  F10: 21,
  F11: 23,
  F12: 24,
};

/** What Ctrl does to a character, where it has a meaning: `@` to `_`, the letters, space. */
function controlCharacter(key: string): string | null {
  if (key.length !== 1) return null;
  const code = key.toUpperCase().charCodeAt(0);
  if (code >= 0x40 && code <= 0x5f) return String.fromCharCode(code - 0x40);
  if (key === " ") return "\x00";
  if (key === "?") return "\x7f";
  if (key === "2") return "\x00";
  if (key >= "3" && key <= "7") return String.fromCharCode(key.charCodeAt(0) - 0x33 + 0x1b);
  if (key === "8") return "\x7f";
  return null;
}

/**
 * The bytes a key press sends.
 *
 * @returns The text to send, or null when the key is not the program's: a bare modifier, the
 *   console's own copy and paste, a key with no meaning on a terminal (the browser keeps it).
 */
export function encodeKey(input: KeyInput, modes: KeyModes): string | null {
  const { key } = input;

  // The console's own: copy and paste. Ctrl+Shift+C is not an interrupt.
  if (input.ctrlKey && input.shiftKey && !input.altKey && (key === "C" || key === "V" || key === "c" || key === "v")) {
    return null;
  }

  switch (key) {
    case "Enter":
      return input.altKey ? "\x1b\r" : "\r";
    case "Backspace":
      if (input.ctrlKey) return "\x08";
      return input.altKey ? "\x1b\x7f" : "\x7f";
    case "Tab":
      return input.shiftKey ? "\x1b[Z" : input.altKey ? "\x1b\t" : "\t";
    case "Escape":
      return "\x1b";
    case "ArrowUp":
      return cursorKey("A", input, modes);
    case "ArrowDown":
      return cursorKey("B", input, modes);
    case "ArrowRight":
      return cursorKey("C", input, modes);
    case "ArrowLeft":
      return cursorKey("D", input, modes);
    case "Home":
      return cursorKey("H", input, modes);
    case "End":
      return cursorKey("F", input, modes);
    case "F1":
      return lowFunctionKey("P", input);
    case "F2":
      return lowFunctionKey("Q", input);
    case "F3":
      return lowFunctionKey("R", input);
    case "F4":
      return lowFunctionKey("S", input);
    case "Shift":
    case "Control":
    case "Alt":
    case "Meta":
    case "AltGraph":
    case "CapsLock":
    case "NumLock":
    case "ScrollLock":
    case "Dead":
    case "Process":
    case "Unidentified":
    case "ContextMenu":
    case "PrintScreen":
    case "Pause":
      return null;
    default:
      break;
  }

  const tilde = TILDE_CODES[key];
  if (tilde !== undefined) return tildeKey(tilde, input);

  // A character: control or alt change it; with neither it is the character itself.
  const first = key.codePointAt(0);
  if (first === undefined || key.length !== (first > 0xffff ? 2 : 1)) return null;
  if (input.metaKey && !input.ctrlKey) return null; // Cmd+key is the browser's or the operating system's
  if (input.ctrlKey) {
    const control = controlCharacter(key);
    if (control === null) return null;
    return input.altKey ? `\x1b${control}` : control;
  }
  return input.altKey ? `\x1b${key}` : key;
}
