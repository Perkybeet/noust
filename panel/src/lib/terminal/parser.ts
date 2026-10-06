/**
 * The escape sequence parser: a program's output in, calls on a handler out.
 *
 * It is Paul Williams' DEC-compatible state machine (the one xterm, VTE and xterm.js are built
 * on), cut to what a console needs, and it is deliberately unforgiving of what it does not
 * understand: an unknown sequence is read to its end and dropped, never half-printed, and a
 * string (OSC, DCS, APC, PM, SOS) that never ends is cut at a limit instead of swallowing the
 * screen. It keeps its state between chunks, so a sequence split across two reads (a socket
 * delivers bytes wherever it likes) is the same as one in one piece.
 *
 * Eight-bit C1 controls (`U+0080` to `U+009F`) are dropped, not obeyed: the output arrives as
 * decoded text, a stray `U+009B` is not a CSI, and treating it as one is how a log line
 * becomes a way to move the cursor.
 */

/** What the parser calls as it reads. */
export interface Handler {
  /** A run of printable characters. */
  print(text: string): void;
  /** A C0 control: carriage return, line feed, backspace, tab, bell, shift in and out. */
  execute(code: number): void;
  /**
   * A control sequence: `ESC [` marker params intermediates final.
   *
   * @param params The numeric parameters; an empty one is 0.
   * @param colons For each parameter, whether it followed a `:` rather than a `;`: SGR's
   *   `38:2::r:g:b` is told from `38;2;r;g;b` by it.
   * @param marker `?`, `>`, `<` or `=` when the sequence began with one; otherwise empty.
   * @param intermediates The characters between the parameters and the final one.
   * @param final The final character.
   */
  csi(params: readonly number[], colons: readonly boolean[], marker: string, intermediates: string, final: string): void;
  /** An escape sequence of the form `ESC` intermediates final, such as `ESC ( 0` or `ESC 7`. */
  esc(intermediates: string, final: string): void;
  /** An operating system command: `ESC ]` payload terminator. The payload is whole. */
  osc(payload: string): void;
}

const ST = "\\";

const State = {
  Ground: 0,
  Escape: 1,
  EscapeIntermediate: 2,
  CsiEntry: 3,
  CsiParam: 4,
  CsiIntermediate: 5,
  CsiIgnore: 6,
  Osc: 7,
  OscEscape: 8,
  /** DCS, SOS, PM, APC: read to the string terminator, said to nobody. */
  Ignored: 9,
  IgnoredEscape: 10,
} as const;

type State = (typeof State)[keyof typeof State];

/** The most parameters one sequence keeps; more are read and dropped. */
const MAX_PARAMS = 32;
/** The largest parameter, as in xterm. */
const MAX_PARAM = 65_535;
/** The longest operating system command kept; a longer one is cut, not stuck. */
const MAX_OSC = 4_096;
/** How much of a string nobody acts on is read before the parser gives up on its end. */
const MAX_IGNORED = 65_536;

const CAN = 0x18;
const SUB = 0x1a;
const ESC = 0x1b;
const BEL = 0x07;

export class Parser {
  private state: State = State.Ground;
  private text = "";
  private intermediates = "";
  private marker = "";
  private params: number[] = [];
  private colons: boolean[] = [];
  private current = -1;
  private nextIsColon = false;
  private payload = "";
  private skipped = 0;

  private readonly handler: Handler;

  constructor(handler: Handler) {
    this.handler = handler;
  }

  /** Reads a chunk of output. A sequence the chunk ends in the middle of is finished by the next. */
  write(data: string): void {
    for (let index = 0; index < data.length; ) {
      const code = data.codePointAt(index) ?? 0;
      index += code > 0xffff ? 2 : 1;
      this.step(code);
    }
    this.flush();
  }

  /** Forgets any sequence in progress. */
  reset(): void {
    this.state = State.Ground;
    this.text = "";
    this.clear();
  }

  private flush(): void {
    if (this.text !== "") {
      const text = this.text;
      this.text = "";
      this.handler.print(text);
    }
  }

  private clear(): void {
    this.intermediates = "";
    this.marker = "";
    this.params = [];
    this.colons = [];
    this.current = -1;
    this.nextIsColon = false;
    this.payload = "";
    this.skipped = 0;
  }

  private step(code: number): void {
    // Whatever a sequence was doing, these end it and say nothing.
    if (code === CAN || code === SUB) {
      this.flush();
      this.state = State.Ground;
      this.clear();
      return;
    }
    switch (this.state) {
      case State.Ground:
        this.ground(code);
        return;
      case State.Escape:
        this.escape(code);
        return;
      case State.EscapeIntermediate:
        this.escapeIntermediate(code);
        return;
      case State.CsiEntry:
      case State.CsiParam:
        this.csiParam(code);
        return;
      case State.CsiIntermediate:
        this.csiIntermediate(code);
        return;
      case State.CsiIgnore:
        this.csiIgnore(code);
        return;
      case State.Osc:
        this.osc(code);
        return;
      case State.OscEscape:
        this.oscEscape(code);
        return;
      case State.Ignored:
        this.ignored(code);
        return;
      case State.IgnoredEscape:
        this.ignoredEscape(code);
        return;
    }
  }

  private ground(code: number): void {
    if (code === ESC) {
      this.flush();
      this.clear();
      this.state = State.Escape;
    } else if (code < 0x20) {
      this.flush();
      this.handler.execute(code);
    } else if (code === 0x7f || (code >= 0x80 && code <= 0x9f)) {
      // DEL fills a blank, and a C1 control is not obeyed: neither is shown.
    } else {
      this.text += String.fromCodePoint(code);
    }
  }

  /** C0 controls act inside a sequence without ending it, as on a real terminal. */
  private control(code: number): boolean {
    if (code < 0x20 && code !== ESC) {
      this.handler.execute(code);
      return true;
    }
    return false;
  }

  private escape(code: number): void {
    if (this.control(code)) return;
    if (code === ESC) return;
    if (code === 0x5b) {
      // [
      this.clear();
      this.state = State.CsiEntry;
    } else if (code === 0x5d) {
      // ]
      this.clear();
      this.state = State.Osc;
    } else if (code === 0x50 || code === 0x58 || code === 0x5e || code === 0x5f) {
      // P, X, ^, _: a string nobody here acts on
      this.clear();
      this.state = State.Ignored;
    } else if (code >= 0x20 && code <= 0x2f) {
      this.intermediates += String.fromCharCode(code);
      this.state = State.EscapeIntermediate;
    } else if (code >= 0x30 && code <= 0x7e) {
      this.handler.esc("", String.fromCharCode(code));
      this.state = State.Ground;
      this.clear();
    } else {
      this.state = State.Ground;
      this.clear();
    }
  }

  private escapeIntermediate(code: number): void {
    if (this.control(code)) return;
    if (code === ESC) {
      this.clear();
      this.state = State.Escape;
    } else if (code >= 0x20 && code <= 0x2f) {
      if (this.intermediates.length < 4) this.intermediates += String.fromCharCode(code);
    } else if (code >= 0x30 && code <= 0x7e) {
      this.handler.esc(this.intermediates, String.fromCharCode(code));
      this.state = State.Ground;
      this.clear();
    } else {
      this.state = State.Ground;
      this.clear();
    }
  }

  private csiParam(code: number): void {
    if (this.control(code)) return;
    if (code === ESC) {
      this.clear();
      this.state = State.Escape;
    } else if (code >= 0x30 && code <= 0x39) {
      this.current = (this.current < 0 ? 0 : this.current) * 10 + (code - 0x30);
      if (this.current > MAX_PARAM) this.current = MAX_PARAM;
      this.state = State.CsiParam;
    } else if (code === 0x3b || code === 0x3a) {
      // ; and :
      this.pushParam();
      this.nextIsColon = code === 0x3a;
      this.state = State.CsiParam;
    } else if (code >= 0x3c && code <= 0x3f) {
      // < = > ? only start a sequence; one after a parameter makes it garbage
      if (this.state === State.CsiEntry && this.marker === "") {
        this.marker = String.fromCharCode(code);
        this.state = State.CsiParam;
      } else {
        this.state = State.CsiIgnore;
      }
    } else if (code >= 0x20 && code <= 0x2f) {
      this.endParams();
      this.intermediates += String.fromCharCode(code);
      this.state = State.CsiIntermediate;
    } else if (code >= 0x40 && code <= 0x7e) {
      this.endParams();
      this.handler.csi(this.params, this.colons, this.marker, this.intermediates, String.fromCharCode(code));
      this.state = State.Ground;
      this.clear();
    } else {
      this.state = State.CsiIgnore;
    }
  }

  /** A separator ended a parameter: an empty one is 0. */
  private pushParam(): void {
    if (this.params.length < MAX_PARAMS) {
      this.params.push(this.current < 0 ? 0 : this.current);
      this.colons.push(this.nextIsColon);
    }
    this.current = -1;
    this.nextIsColon = false;
  }

  /** The sequence's parameters ended: the last one counts if it had a digit, or follows another. */
  private endParams(): void {
    if (this.current >= 0 || this.params.length > 0) this.pushParam();
  }

  private csiIntermediate(code: number): void {
    if (this.control(code)) return;
    if (code === ESC) {
      this.clear();
      this.state = State.Escape;
    } else if (code >= 0x20 && code <= 0x2f) {
      if (this.intermediates.length < 4) this.intermediates += String.fromCharCode(code);
    } else if (code >= 0x40 && code <= 0x7e) {
      this.handler.csi(this.params, this.colons, this.marker, this.intermediates, String.fromCharCode(code));
      this.state = State.Ground;
      this.clear();
    } else {
      this.state = State.CsiIgnore;
    }
  }

  private csiIgnore(code: number): void {
    if (this.control(code)) return;
    if (code === ESC) {
      this.clear();
      this.state = State.Escape;
    } else if (code >= 0x40 && code <= 0x7e) {
      this.state = State.Ground;
      this.clear();
    }
  }

  private osc(code: number): void {
    if (code === BEL) {
      this.finishOsc();
    } else if (code === ESC) {
      this.state = State.OscEscape;
    } else if (code >= 0x20 && code !== 0x7f) {
      if (this.payload.length < MAX_OSC) this.payload += String.fromCodePoint(code);
    }
  }

  private oscEscape(code: number): void {
    if (String.fromCharCode(code) === ST) {
      this.finishOsc();
    } else {
      // Anything else after ESC abandons the string and is the start of a new sequence.
      this.clear();
      this.state = State.Escape;
      this.escape(code);
    }
  }

  private finishOsc(): void {
    const payload = this.payload;
    this.state = State.Ground;
    this.clear();
    this.handler.osc(payload);
  }

  private ignored(code: number): void {
    if (code === ESC) {
      this.state = State.IgnoredEscape;
    } else if (++this.skipped > MAX_IGNORED) {
      // A string that never ends is not allowed to swallow the screen for ever.
      this.state = State.Ground;
      this.clear();
    }
  }

  private ignoredEscape(code: number): void {
    if (String.fromCharCode(code) === ST) {
      this.state = State.Ground;
      this.clear();
    } else {
      this.clear();
      this.state = State.Escape;
      this.escape(code);
    }
  }
}
