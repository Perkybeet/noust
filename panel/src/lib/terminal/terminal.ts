/**
 * The emulator: the parser's calls turned into what they do to a screen.
 *
 * `Terminal` is what the console holds for one session. A program's output goes in through
 * `write`; the grid it draws is `screen`; the bytes the terminal answers with (the reply to a
 * query for the cursor's position) come out of `onResponse`.
 *
 * What it understands is xterm's subset that real programs use: cursor movement, erasing, line
 * and character insertion and deletion, scroll regions, the alternate screen, tab stops, the
 * 16, 256 and true colours of SGR with bold, dim, italic, underline, inverse, hidden and
 * strike, the DEC line-drawing set, and the modes that change what is drawn or what keys send.
 *
 * What it refuses, on purpose, because each is a way for output to act on the person reading it:
 *
 * - It never writes the clipboard (OSC 52), opens a link (OSC 8) or follows a hyperlink.
 * - It never reports the window's title, size in pixels or icon label back as input
 *   (`CSI 21 t`, `CSI 14 t`), the replies a hostile file once used to type into a shell.
 * - It answers only the queries whose answer is fixed or a position: device status, cursor
 *   position and the two device attribute reports.
 * - It never sends mouse reports: a program that asks for the mouse gets none, and the console's
 *   own selection keeps working.
 */

import { Parser } from "./parser";
import type { Handler } from "./parser";
import { Attr, DEFAULT, Screen, TRUE_COLOUR } from "./screen";
import type { ScreenEvents } from "./screen";

/** What the terminal says it is to a program that asks (the first device attribute report). */
const PRIMARY_ATTRIBUTES = "\x1b[?1;2c";
/** The second: a VT100-class terminal with no firmware version. */
const SECONDARY_ATTRIBUTES = "\x1b[>0;0;0c";

/** What a paste is wrapped in when the shell asked to be told one is a paste. */
const PASTE_START = "\x1b[200~";
const PASTE_END = "\x1b[201~";

function amount(params: readonly number[], index: number, fallback = 1): number {
  const value = params[index] ?? 0;
  return value === 0 ? fallback : value;
}

function channel(value: number | undefined): number {
  return Math.min(Math.max(value ?? 0, 0), 255);
}

export class Terminal {
  readonly screen: Screen;
  private readonly parser: Parser;

  constructor(cols = 80, rows = 24, events: ScreenEvents = {}, scrollback?: number) {
    this.screen = new Screen(cols, rows, events, scrollback);
    this.parser = new Parser(this.handler());
  }

  /** Feeds the program's output. */
  write(data: string): void {
    this.parser.write(data);
  }

  /** Changes the size, as the window did. */
  resize(cols: number, rows: number): void {
    this.screen.resize(cols, rows);
  }

  /**
   * What to send for pasted text: the text itself, in markers when the shell asked for them. The
   * end marker is removed from the text first, so a paste cannot close itself and have the rest
   * typed as commands.
   */
  paste(text: string): string {
    const clean = text.replace(/\r\n?|\n/g, "\r");
    if (!this.screen.modes.bracketedPaste) return clean;
    // eslint-disable-next-line no-control-regex -- the marker is an escape sequence
    return `${PASTE_START}${clean.replace(/\x1b\[20[01]~/g, "")}${PASTE_END}`;
  }

  private handler(): Handler {
    return {
      print: (text) => {
        this.screen.print(text);
      },
      execute: (code) => {
        this.execute(code);
      },
      csi: (params, colons, marker, intermediates, final) => {
        this.csi(params, colons, marker, intermediates, final);
      },
      esc: (intermediates, final) => {
        this.esc(intermediates, final);
      },
      osc: (payload) => {
        this.osc(payload);
      },
    };
  }

  // -- C0 controls ----------------------------------------------------------------------

  private execute(code: number): void {
    const screen = this.screen;
    switch (code) {
      case 0x07:
        screen.bell();
        break;
      case 0x08:
        screen.backspace();
        break;
      case 0x09:
        screen.tab();
        break;
      case 0x0a:
      case 0x0b:
      case 0x0c:
        screen.lineFeed();
        break;
      case 0x0d:
        screen.carriageReturn();
        break;
      case 0x0e:
        screen.shiftOut();
        break;
      case 0x0f:
        screen.shiftIn();
        break;
      default:
        // NUL, ENQ (no answerback), and the rest of C0 do nothing.
        break;
    }
  }

  // -- ESC sequences --------------------------------------------------------------------

  private esc(intermediates: string, final: string): void {
    const screen = this.screen;
    if (intermediates === "") {
      switch (final) {
        case "7":
          screen.saveCursor();
          break;
        case "8":
          screen.restoreCursor();
          break;
        case "D":
          screen.index();
          break;
        case "E":
          screen.nextLine();
          break;
        case "M":
          screen.reverseIndex();
          break;
        case "H":
          screen.setTabStop();
          break;
        case "c":
          screen.reset();
          break;
        case "=":
          screen.modes.applicationKeypad = true;
          break;
        case ">":
          screen.modes.applicationKeypad = false;
          break;
        default:
          break;
      }
      return;
    }
    if (intermediates === "(" || intermediates === ")") {
      screen.designate(intermediates === "(" ? 0 : 1, final === "0" ? "0" : "B");
    } else if (intermediates === "#" && final === "8") {
      screen.alignmentTest();
    }
  }

  // -- OSC ------------------------------------------------------------------------------

  private osc(payload: string): void {
    const split = payload.indexOf(";");
    const code = split === -1 ? payload : payload.slice(0, split);
    const text = split === -1 ? "" : payload.slice(split + 1);
    // 0 sets the icon and the title, 1 the icon, 2 the title. The clipboard (52), hyperlinks (8)
    // and every colour query are ignored: see the module's note.
    if (code === "0" || code === "2") this.screen.setTitle(text);
  }

  // -- CSI ------------------------------------------------------------------------------

  private csi(
    params: readonly number[],
    colons: readonly boolean[],
    marker: string,
    intermediates: string,
    final: string,
  ): void {
    if (intermediates !== "") {
      this.csiWithIntermediates(params, intermediates, final);
      return;
    }
    if (marker === "?") {
      if (final === "h" || final === "l") this.privateModes(params, final === "h");
      else if (final === "n") this.deviceStatus(params, true);
      return;
    }
    if (marker === ">") {
      if (final === "c" && (params[0] ?? 0) === 0) this.screen.respond(SECONDARY_ATTRIBUTES);
      return;
    }
    if (marker !== "") return;
    const screen = this.screen;
    switch (final) {
      case "A":
        screen.moveBy(-amount(params, 0), 0);
        break;
      case "B":
      case "e":
        screen.moveBy(amount(params, 0), 0);
        break;
      case "C":
      case "a":
        screen.moveBy(0, amount(params, 0));
        break;
      case "D":
        screen.moveBy(0, -amount(params, 0));
        break;
      case "E":
        screen.moveBy(amount(params, 0), 0);
        screen.carriageReturn();
        break;
      case "F":
        screen.moveBy(-amount(params, 0), 0);
        screen.carriageReturn();
        break;
      case "G":
      case "`":
        screen.setColumn(amount(params, 0) - 1);
        break;
      case "H":
      case "f":
        screen.moveTo(amount(params, 0) - 1, amount(params, 1) - 1);
        break;
      case "d":
        screen.setLine(amount(params, 0) - 1);
        break;
      case "I":
        screen.tab(amount(params, 0));
        break;
      case "Z":
        screen.backTab(amount(params, 0));
        break;
      case "J":
        screen.eraseInDisplay(params[0] ?? 0);
        break;
      case "K":
        screen.eraseInLine(params[0] ?? 0);
        break;
      case "L":
        screen.insertLines(amount(params, 0));
        break;
      case "M":
        screen.deleteLines(amount(params, 0));
        break;
      case "P":
        screen.deleteChars(amount(params, 0));
        break;
      case "@":
        screen.insertChars(amount(params, 0));
        break;
      case "X":
        screen.eraseChars(amount(params, 0));
        break;
      case "S":
        screen.scrollUp(amount(params, 0));
        break;
      case "T":
        screen.scrollDown(amount(params, 0));
        break;
      case "g":
        if ((params[0] ?? 0) === 0) screen.clearTabStop(false);
        else if (params[0] === 3) screen.clearTabStop(true);
        break;
      case "r":
        screen.setScrollRegion(amount(params, 0) - 1, (params[1] ?? 0) === 0 ? screen.rows - 1 : (params[1] ?? 1) - 1);
        break;
      case "s":
        screen.saveCursor();
        break;
      case "u":
        screen.restoreCursor();
        break;
      case "h":
      case "l":
        this.ansiModes(params, final === "h");
        break;
      case "m":
        this.sgr(params, colons);
        break;
      case "n":
        this.deviceStatus(params, false);
        break;
      case "c":
        if ((params[0] ?? 0) === 0) screen.respond(PRIMARY_ATTRIBUTES);
        break;
      default:
        // CSI t (window operations) is among the ones ignored: see the module's note.
        break;
    }
  }

  private csiWithIntermediates(params: readonly number[], intermediates: string, final: string): void {
    if (intermediates === "!" && final === "p") {
      this.screen.softReset();
    } else if (intermediates === " " && final === "q") {
      // DECSCUSR: 0, 1, 2 block, 3, 4 underline, 5, 6 bar
      const style = params[0] ?? 0;
      this.screen.cursorShape = style === 3 || style === 4 ? "underline" : style === 5 || style === 6 ? "bar" : "block";
    }
  }

  private ansiModes(params: readonly number[], on: boolean): void {
    for (const mode of params) {
      if (mode === 4) this.screen.modes.insert = on;
      else if (mode === 20) this.screen.modes.newline = on;
    }
  }

  private privateModes(params: readonly number[], on: boolean): void {
    const screen = this.screen;
    for (const mode of params) {
      switch (mode) {
        case 1:
          screen.modes.applicationCursor = on;
          break;
        case 5:
          screen.modes.reverseVideo = on;
          screen.invalidate();
          break;
        case 6:
          screen.modes.origin = on;
          screen.moveTo(0, 0);
          break;
        case 7:
          screen.modes.autoWrap = on;
          if (!on) screen.pendingWrap = false;
          break;
        case 25:
          screen.modes.cursorVisible = on;
          screen.invalidate();
          break;
        case 47:
        case 1047:
          screen.useAlternateScreen(on, { save: false, clear: mode === 1047 && !on });
          break;
        case 1049:
          screen.useAlternateScreen(on, { save: true, clear: on });
          break;
        case 1000:
        case 1002:
        case 1003:
        case 1006:
          screen.modes.mouse = on;
          break;
        case 2004:
          screen.modes.bracketedPaste = on;
          break;
        default:
          // Blinking cursor, focus events, synchronised output and the rest change nothing here.
          break;
      }
    }
  }

  private deviceStatus(params: readonly number[], isPrivate: boolean): void {
    const screen = this.screen;
    const report = params[0] ?? 0;
    if (report === 5 && !isPrivate) {
      screen.respond("\x1b[0n");
    } else if (report === 6) {
      const row = screen.y - (screen.modes.origin ? screen.scrollTop : 0) + 1;
      screen.respond(`\x1b[${isPrivate ? "?" : ""}${row};${screen.x + 1}R`);
    }
  }

  // -- SGR ------------------------------------------------------------------------------

  private sgr(params: readonly number[], colons: readonly boolean[]): void {
    const pen = this.screen.pen;
    if (params.length === 0) {
      this.screen.resetPen();
      return;
    }
    for (let i = 0; i < params.length; i++) {
      const code = params[i] ?? 0;
      if (code === 0) {
        this.screen.resetPen();
      } else if (code === 1) {
        pen.attrs |= Attr.Bold;
      } else if (code === 2) {
        pen.attrs |= Attr.Dim;
      } else if (code === 3) {
        pen.attrs |= Attr.Italic;
      } else if (code === 4) {
        // 4:0 turns the underline off, 4:1 to 4:5 are its styles, all drawn as one line.
        if (colons[i + 1] === true) {
          pen.attrs = (params[i + 1] ?? 0) === 0 ? pen.attrs & ~Attr.Underline : pen.attrs | Attr.Underline;
          i++;
        } else {
          pen.attrs |= Attr.Underline;
        }
      } else if (code === 5 || code === 6) {
        pen.attrs |= Attr.Blink;
      } else if (code === 7) {
        pen.attrs |= Attr.Inverse;
      } else if (code === 8) {
        pen.attrs |= Attr.Invisible;
      } else if (code === 9) {
        pen.attrs |= Attr.Strike;
      } else if (code === 21) {
        pen.attrs |= Attr.Underline;
      } else if (code === 22) {
        pen.attrs &= ~(Attr.Bold | Attr.Dim);
      } else if (code === 23) {
        pen.attrs &= ~Attr.Italic;
      } else if (code === 24) {
        pen.attrs &= ~Attr.Underline;
      } else if (code === 25) {
        pen.attrs &= ~Attr.Blink;
      } else if (code === 27) {
        pen.attrs &= ~Attr.Inverse;
      } else if (code === 28) {
        pen.attrs &= ~Attr.Invisible;
      } else if (code === 29) {
        pen.attrs &= ~Attr.Strike;
      } else if (code >= 30 && code <= 37) {
        pen.fg = code - 30;
      } else if (code === 39) {
        pen.fg = DEFAULT;
      } else if (code >= 40 && code <= 47) {
        pen.bg = code - 40;
      } else if (code === 49) {
        pen.bg = DEFAULT;
      } else if (code >= 90 && code <= 97) {
        pen.fg = code - 90 + 8;
      } else if (code >= 100 && code <= 107) {
        pen.bg = code - 100 + 8;
      } else if (code === 38 || code === 48 || code === 58) {
        const [colour, used] = this.extendedColour(params, colons, i);
        if (colour !== null && code === 38) pen.fg = colour;
        else if (colour !== null && code === 48) pen.bg = colour;
        // 58 sets the underline's colour, which is drawn in the text's own: its arguments are skipped.
        i += used;
      }
    }
  }

  /**
   * Reads what follows 38, 48 or 58: `5;n` for the 256-colour palette, `2;r;g;b` for true colour,
   * and their colon forms (`38:2::r:g:b`, which carries a colour-space id first).
   *
   * @returns The colour (null when what follows is not one) and how many parameters it used.
   */
  private extendedColour(params: readonly number[], colons: readonly boolean[], at: number): [number | null, number] {
    const mode = params[at + 1];
    if (mode === 5) {
      const index = params[at + 2];
      return [index === undefined ? null : Math.min(index, 255), 2];
    }
    if (mode === 2) {
      let first = at + 2;
      let used = 4;
      // `38:2:cs:r:g:b`: five colon-joined values after the mode means the first is a colour space.
      let joined = 0;
      while (colons[at + 2 + joined] === true) joined++;
      if (colons[at + 1] === true && joined >= 4) {
        first++;
        used = 5;
      }
      const red = params[first];
      const green = params[first + 1];
      const blue = params[first + 2];
      if (red === undefined || green === undefined || blue === undefined) return [null, params.length];
      return [TRUE_COLOUR | (channel(red) << 16) | (channel(green) << 8) | channel(blue), used];
    }
    return [null, 0];
  }
}
