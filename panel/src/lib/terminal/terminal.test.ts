/**
 * Golden sequences: what a program's bytes do to the screen.
 *
 * Each case is bytes in, a grid out, written the way the sequence is documented, so a reader can
 * check it against xterm's control sequences. The grid is read back as text (`rows`) and the
 * cursor as `(column, row)`, 0-based.
 */

import { describe, expect, it } from "vitest";

import { charWidth, translate } from "./charset";
import { Attr, DEFAULT, TRUE_COLOUR } from "./screen";
import { Terminal } from "./terminal";

function open(cols = 20, rows = 5): { term: Terminal; replies: string[]; titles: string[] } {
  const replies: string[] = [];
  const titles: string[] = [];
  const term = new Terminal(cols, rows, {
    onResponse: (data) => replies.push(data),
    onTitle: (title) => titles.push(title),
  });
  return { term, replies, titles };
}

function run(output: string, cols = 20, rows = 5): Terminal {
  const { term } = open(cols, rows);
  term.write(output);
  return term;
}

function rows(term: Terminal): string[] {
  return term.screen.textRows();
}

function cursor(term: Terminal): [number, number] {
  return [term.screen.x, term.screen.y];
}

describe("printing", () => {
  it("writes text and moves the cursor", () => {
    const term = run("hello");
    expect(rows(term)[0]).toBe("hello");
    expect(cursor(term)).toEqual([5, 0]);
  });

  it("goes to the next line on a line feed, keeping the column", () => {
    const term = run("ab\ncd");
    expect(rows(term).slice(0, 2)).toEqual(["ab", "  cd"]);
  });

  it("returns the carriage on CR and overwrites", () => {
    expect(rows(run("abc\rX"))[0]).toBe("Xbc");
  });

  it("treats CR LF as a newline", () => {
    expect(rows(run("one\r\ntwo")).slice(0, 2)).toEqual(["one", "two"]);
  });

  it("backspaces without erasing", () => {
    const term = run("abc\bX");
    expect(rows(term)[0]).toBe("abX");
  });

  it("does not move left of the first column", () => {
    expect(cursor(run("\b\b\b"))).toEqual([0, 0]);
  });

  it("moves to the next tab stop, every eight columns", () => {
    const term = run("a\tb\tc", 40);
    expect(rows(term)[0]).toBe("a       b       c");
  });

  it("wraps at the right edge, and marks the line as wrapped", () => {
    const term = run("0123456789ab", 10, 3);
    expect(rows(term).slice(0, 2)).toEqual(["0123456789", "ab"]);
    expect(term.screen.row(0).wrapped).toBe(true);
    expect(term.screen.row(1).wrapped).toBe(false);
  });

  it("waits to wrap until the next character: a line of exactly the width does not skip a line", () => {
    const term = run("0123456789\r\nx", 10, 3);
    expect(rows(term).slice(0, 2)).toEqual(["0123456789", "x"]);
  });

  it("does not wrap with autowrap off, and overwrites the last column", () => {
    const term = run("\x1b[?7l0123456789ab", 10, 2);
    expect(rows(term)[0]).toBe("012345678b");
    expect(rows(term)[1]).toBe("");
  });

  it("scrolls when a line feed is at the bottom, keeping the lines in the scrollback", () => {
    const term = run("1\n2\n3\n4", 5, 3);
    expect(rows(term)).toEqual([" 2", "  3", "   4"]);
    expect(term.screen.scrollback.map((row) => row.text())).toEqual(["1"]);
  });

  it("ignores NUL, DEL and bell, and rings the bell", () => {
    let rang = 0;
    const term = new Terminal(10, 2, {
      onBell: () => {
        rang++;
      },
    });
    term.write("a\x00b\x7fc\x07");
    expect(rows(term)[0]).toBe("abc");
    expect(rang).toBe(1);
  });

  it("drops eight-bit C1 controls instead of obeying them", () => {
    // U+009B is a CSI in eight-bit mode; in decoded text it is not a way to move the cursor.
    expect(rows(run("a\u009b2Jb"))[0]).toBe("a2Jb");
  });

  it("counts the columns of wide characters, and gives a combining mark no column", () => {
    const term = run("a世b́");
    expect(rows(term)[0]).toBe("a世b́");
    expect(cursor(term)).toEqual([4, 0]);
    expect(term.screen.row(0).attrs[1]).toBe(Attr.Wide);
    expect(term.screen.row(0).attrs[2]).toBe(Attr.Continuation);
  });

  it("puts a wide character that does not fit on the next line", () => {
    const term = run("123456789世", 10, 2);
    expect(rows(term)).toEqual(["123456789", "世"]);
  });

  it("overwriting half of a wide character blanks the other half", () => {
    const term = run("世\rX");
    expect(rows(term)[0]).toBe("X");
  });

  it("measures widths the way wcwidth does for the common cases", () => {
    expect(charWidth(0x41)).toBe(1);
    expect(charWidth(0x4e16)).toBe(2);
    expect(charWidth(0xff21)).toBe(2);
    expect(charWidth(0x1f600)).toBe(2);
    expect(charWidth(0x0301)).toBe(0);
    expect(charWidth(0x200d)).toBe(0);
    expect(charWidth(0xfe0f)).toBe(0);
  });
});

describe("cursor movement", () => {
  it("moves up, down, forward and back, and stops at the edges", () => {
    const term = run("\x1b[3;5H\x1b[A\x1b[2C\x1b[B\x1b[3D", 20, 5);
    expect(cursor(term)).toEqual([3, 2]);
    expect(cursor(run("\x1b[99A\x1b[99D"))).toEqual([0, 0]);
    expect(cursor(run("\x1b[99B\x1b[99C", 20, 5))).toEqual([19, 4]);
  });

  it("positions with CUP and HVP, 1-based, defaulting to 1", () => {
    expect(cursor(run("\x1b[2;3H"))).toEqual([2, 1]);
    expect(cursor(run("\x1b[2;3f"))).toEqual([2, 1]);
    expect(cursor(run("\x1b[;5H"))).toEqual([4, 0]);
    expect(cursor(run("\x1b[4H"))).toEqual([0, 3]);
    expect(cursor(run("\x1b[H"))).toEqual([0, 0]);
    expect(cursor(run("\x1b[99;99H"))).toEqual([19, 4]);
  });

  it("moves by column and by line", () => {
    expect(cursor(run("\x1b[7G"))).toEqual([6, 0]);
    expect(cursor(run("\x1b[4d"))).toEqual([0, 3]);
    expect(cursor(run("\x1b[5\x60"))).toEqual([4, 0]);
  });

  it("moves to the start of the line N lines down or up", () => {
    expect(cursor(run("abc\x1b[2E"))).toEqual([0, 2]);
    expect(cursor(run("\x1b[4;5H\x1b[2F"))).toEqual([0, 1]);
  });

  it("saves and restores the cursor, with ESC 7 and ESC 8 and with CSI s and u", () => {
    expect(cursor(run("\x1b[2;3H\x1b7\x1b[5;5H\x1b8"))).toEqual([2, 1]);
    expect(cursor(run("\x1b[2;3H\x1b[s\x1b[5;5H\x1b[u"))).toEqual([2, 1]);
  });

  it("restores the pen with the cursor", () => {
    const term = run("\x1b[31m\x1b7\x1b[0m\x1b8x");
    expect(term.screen.row(0).fg[0]).toBe(1);
  });

  it("restoring with nothing saved goes home", () => {
    expect(cursor(run("abc\x1b8"))).toEqual([0, 0]);
  });

  it("indexes, reverse indexes and goes to the next line", () => {
    expect(cursor(run("\x1bD"))).toEqual([0, 1]);
    expect(cursor(run("\x1b[3;1H\x1bM"))).toEqual([0, 1]);
    expect(cursor(run("abc\x1bE"))).toEqual([0, 1]);
  });

  it("scrolls down on a reverse index at the top", () => {
    const term = run("a\r\nb\x1b[H\x1bM", 5, 3);
    expect(rows(term)).toEqual(["", "a", "b"]);
  });

  it("moves with the tab stops: CHT, CBT, HTS and TBC", () => {
    expect(cursor(run("\x1b[2I", 40))).toEqual([16, 0]);
    expect(cursor(run("\x1b[20G\x1b[Z", 40))).toEqual([16, 0]);
    expect(cursor(run("\x1b[4G\x1bH\x1b[1G\t", 40))).toEqual([3, 0]);
    expect(cursor(run("\x1b[3g\t", 40))).toEqual([39, 0]);
  });
});

describe("erasing", () => {
  const filled = "aaaaa\r\nbbbbb\r\nccccc";

  it("erases below the cursor (ED 0)", () => {
    const term = run(`${filled}\x1b[2;3H\x1b[J`, 5, 3);
    expect(rows(term)).toEqual(["aaaaa", "bb", ""]);
  });

  it("erases above the cursor (ED 1), the cursor's cell included", () => {
    const term = run(`${filled}\x1b[2;3H\x1b[1J`, 5, 3);
    expect(rows(term)).toEqual(["", "   bb", "ccccc"]);
  });

  it("erases the whole screen (ED 2), leaving the cursor", () => {
    const term = run(`${filled}\x1b[2;3H\x1b[2J`, 5, 3);
    expect(rows(term)).toEqual(["", "", ""]);
    expect(cursor(term)).toEqual([2, 1]);
  });

  it("clears the scrollback too with ED 3", () => {
    const term = run("1\n2\n3\n4\x1b[3J", 5, 3);
    expect(term.screen.scrollback).toHaveLength(0);
  });

  it("erases in a line (EL 0, 1, 2)", () => {
    expect(rows(run("abcdef\x1b[3G\x1b[K", 10))[0]).toBe("ab");
    expect(rows(run("abcdef\x1b[3G\x1b[1K", 10))[0]).toBe("   def");
    expect(rows(run("abcdef\x1b[3G\x1b[2K", 10))[0]).toBe("");
  });

  it("erases characters in place (ECH) and deletes them, pulling the rest left (DCH)", () => {
    expect(rows(run("abcdef\x1b[3G\x1b[2X", 10))[0]).toBe("ab  ef");
    expect(rows(run("abcdef\x1b[3G\x1b[2P", 10))[0]).toBe("abef");
  });

  it("inserts blanks, pushing the rest right (ICH), losing what falls off", () => {
    expect(rows(run("abcdef\x1b[3G\x1b[2@", 8))[0]).toBe("ab  cdef");
    expect(rows(run("abcdef\x1b[3G\x1b[4@", 8))[0]).toBe("ab    cd");
  });

  it("erases with the current background, as most terminals do (BCE)", () => {
    const term = run("\x1b[44m\x1b[2K");
    expect(term.screen.row(0).bg[3]).toBe(4);
    expect(term.screen.row(0).chars[3]).toBe(" ");
  });
});

describe("lines and scroll regions", () => {
  it("inserts and deletes lines (IL, DL) from the cursor", () => {
    expect(rows(run("a\r\nb\r\nc\x1b[2;1H\x1b[L", 5, 4))).toEqual(["a", "", "b", "c"]);
    expect(rows(run("a\r\nb\r\nc\x1b[2;1H\x1b[M", 5, 4))).toEqual(["a", "c", "", ""]);
  });

  it("scrolls up and down by N (SU, SD)", () => {
    expect(rows(run("a\r\nb\r\nc\x1b[S", 5, 3))).toEqual(["b", "c", ""]);
    expect(rows(run("a\r\nb\r\nc\x1b[T", 5, 3))).toEqual(["", "a", "b"]);
  });

  it("scrolls only inside a region (DECSTBM), and puts the cursor home", () => {
    const term = run("1\r\n2\r\n3\r\n4\r\n5\x1b[2;4r\x1b[4;1H\n", 5, 5);
    expect(rows(term)).toEqual(["1", "3", "4", "", "5"]);
    expect(term.screen.scrollback).toHaveLength(0);
  });

  it("resets the region to the whole screen when it is empty or inverted", () => {
    const term = run("\x1b[2;4r\x1b[r", 5, 5);
    expect([term.screen.scrollTop, term.screen.scrollBottom]).toEqual([0, 4]);
    const inverted = run("\x1b[4;2r", 5, 5);
    expect([inverted.screen.scrollTop, inverted.screen.scrollBottom]).toEqual([0, 4]);
  });

  it("addresses relative to the region under origin mode (DECOM)", () => {
    const term = run("\x1b[2;4r\x1b[?6h\x1b[1;1H", 5, 5);
    expect(cursor(term)).toEqual([0, 1]);
    const outside = run("\x1b[2;4r\x1b[?6h\x1b[9;1H", 5, 5);
    expect(cursor(outside)).toEqual([0, 3]);
  });

  it("does nothing for IL and DL outside the region", () => {
    const term = run("a\r\nb\r\nc\r\nd\x1b[2;3r\x1b[4;1H\x1b[L", 5, 4);
    expect(rows(term)).toEqual(["a", "b", "c", "d"]);
  });

  it("an insert mode shifts characters right (IRM)", () => {
    expect(rows(run("abcd\x1b[1G\x1b[4hXY", 10))[0]).toBe("XYabcd");
  });

  it("a newline mode makes a line feed return the carriage (LNM)", () => {
    expect(rows(run("\x1b[20hab\ncd")).slice(0, 2)).toEqual(["ab", "cd"]);
  });
});

describe("attributes (SGR)", () => {
  function cell(term: Terminal, x = 0): { fg: number; bg: number; attrs: number } {
    const row = term.screen.row(0);
    return { fg: row.fg[x] ?? 0, bg: row.bg[x] ?? 0, attrs: row.attrs[x] ?? 0 };
  }

  it("sets the eight colours and their bright forms, foreground and background", () => {
    expect(cell(run("\x1b[31mx")).fg).toBe(1);
    expect(cell(run("\x1b[44mx")).bg).toBe(4);
    expect(cell(run("\x1b[91mx")).fg).toBe(9);
    expect(cell(run("\x1b[107mx")).bg).toBe(15);
  });

  it("sets bold, dim, italic, underline, blink, inverse, hidden and strike, and clears each", () => {
    const all = cell(run("\x1b[1;2;3;4;5;7;8;9mx"));
    expect(all.attrs).toBe(Attr.Bold | Attr.Dim | Attr.Italic | Attr.Underline | Attr.Blink | Attr.Inverse | Attr.Invisible | Attr.Strike);
    const cleared = cell(run("\x1b[1;2;3;4;5;7;8;9m\x1b[22;23;24;25;27;28;29mx"));
    expect(cleared.attrs).toBe(0);
  });

  it("resets with 0 and with an empty parameter list", () => {
    expect(cell(run("\x1b[1;31m\x1b[0mx"))).toEqual({ fg: DEFAULT, bg: DEFAULT, attrs: 0 });
    expect(cell(run("\x1b[1;31m\x1b[mx"))).toEqual({ fg: DEFAULT, bg: DEFAULT, attrs: 0 });
  });

  it("returns to the default colours with 39 and 49", () => {
    expect(cell(run("\x1b[31;42m\x1b[39;49mx"))).toEqual({ fg: DEFAULT, bg: DEFAULT, attrs: 0 });
  });

  it("reads the 256-colour forms, semicolon and colon", () => {
    expect(cell(run("\x1b[38;5;196mx")).fg).toBe(196);
    expect(cell(run("\x1b[48;5;21mx")).bg).toBe(21);
    expect(cell(run("\x1b[38:5:82mx")).fg).toBe(82);
  });

  it("reads true colour, semicolon and colon, with and without a colour space", () => {
    const rgb = TRUE_COLOUR | (255 << 16) | (128 << 8) | 7;
    expect(cell(run("\x1b[38;2;255;128;7mx")).fg).toBe(rgb);
    expect(cell(run("\x1b[48;2;255;128;7mx")).bg).toBe(rgb);
    expect(cell(run("\x1b[38:2:255:128:7mx")).fg).toBe(rgb);
    expect(cell(run("\x1b[38:2::255:128:7mx")).fg).toBe(rgb);
  });

  it("goes on reading the parameters after an extended colour", () => {
    const term = run("\x1b[38;5;196;1;4mx");
    expect(cell(term)).toEqual({ fg: 196, bg: DEFAULT, attrs: Attr.Bold | Attr.Underline });
    const truecolour = run("\x1b[38;2;1;2;3;48;5;17;3mx");
    expect(cell(truecolour).bg).toBe(17);
    expect(cell(truecolour).attrs).toBe(Attr.Italic);
  });

  it("clamps a colour's channels and ignores an incomplete one", () => {
    expect(cell(run("\x1b[38;2;999;0;0mx")).fg).toBe(TRUE_COLOUR | (255 << 16));
    expect(cell(run("\x1b[38;2;1;2mx")).fg).toBe(DEFAULT);
  });

  it("skips the underline colour's arguments instead of reading them as attributes", () => {
    const term = run("\x1b[58;5;196;1mx");
    expect(cell(term).attrs).toBe(Attr.Bold);
  });

  it("reads underline styles: 4:0 turns it off, 4:3 is an underline", () => {
    expect(cell(run("\x1b[4:3mx")).attrs).toBe(Attr.Underline);
    expect(cell(run("\x1b[4m\x1b[4:0mx")).attrs).toBe(0);
  });

  it("gives each cell the pen it was written with", () => {
    const term = run("a\x1b[31mb\x1b[0mc");
    expect([0, 1, 2].map((x) => term.screen.row(0).fg[x])).toEqual([DEFAULT, 1, DEFAULT]);
  });
});

describe("the alternate screen", () => {
  it("switches to a blank screen and back, restoring what was there and the cursor", () => {
    const term = run("main\x1b[?1049hfull screen\x1b[?1049l");
    expect(rows(term)[0]).toBe("main");
    expect(cursor(term)).toEqual([4, 0]);
  });

  it("starts blank each time", () => {
    const term = run("\x1b[?1049hx\x1b[?1049l\x1b[?1049h");
    expect(rows(term)[0]).toBe("");
  });

  it("keeps no scrollback while on it", () => {
    const term = run("\x1b[?1049h1\n2\n3\n4\n5\n6\n7");
    expect(term.screen.scrollback).toHaveLength(0);
    expect(term.screen.alt).toBe(true);
  });

  it("also answers the older modes 47 and 1047", () => {
    expect(run("\x1b[?47h").screen.alt).toBe(true);
    expect(run("\x1b[?1047h\x1b[?1047l").screen.alt).toBe(false);
  });
});

describe("modes", () => {
  it("hides and shows the cursor", () => {
    expect(run("\x1b[?25l").screen.modes.cursorVisible).toBe(false);
    expect(run("\x1b[?25l\x1b[?25h").screen.modes.cursorVisible).toBe(true);
  });

  it("tracks application cursor keys, bracketed paste, keypad and reverse video", () => {
    expect(run("\x1b[?1h").screen.modes.applicationCursor).toBe(true);
    expect(run("\x1b[?2004h").screen.modes.bracketedPaste).toBe(true);
    expect(run("\x1b=").screen.modes.applicationKeypad).toBe(true);
    expect(run("\x1b=\x1b>").screen.modes.applicationKeypad).toBe(false);
    expect(run("\x1b[?5h").screen.modes.reverseVideo).toBe(true);
  });

  it("notes that a program asked for the mouse, and sends none", () => {
    const { term, replies } = open();
    term.write("\x1b[?1000h\x1b[?1006h");
    expect(term.screen.modes.mouse).toBe(true);
    expect(replies).toEqual([]);
  });

  it("changes the cursor's shape", () => {
    expect(run("\x1b[3 q").screen.cursorShape).toBe("underline");
    expect(run("\x1b[5 q").screen.cursorShape).toBe("bar");
    expect(run("\x1b[5 q\x1b[2 q").screen.cursorShape).toBe("block");
  });

  it("applies several modes in one sequence", () => {
    const term = run("\x1b[?1;2004;25l");
    expect([term.screen.modes.applicationCursor, term.screen.modes.bracketedPaste, term.screen.modes.cursorVisible]).toEqual([false, false, false]);
  });

  it("soft resets the modes and keeps the screen (DECSTR)", () => {
    const term = run("keep\x1b[?1h\x1b[?25l\x1b[31m\x1b[!p");
    expect(rows(term)[0]).toBe("keep");
    expect(term.screen.modes.applicationCursor).toBe(false);
    expect(term.screen.modes.cursorVisible).toBe(true);
    expect(term.screen.pen.fg).toBe(DEFAULT);
  });

  it("resets everything but the scrollback (RIS)", () => {
    const term = run("1\n2\n3\n4\x1b[31m\x1bc", 5, 3);
    expect(rows(term)).toEqual(["", "", ""]);
    expect(term.screen.pen.fg).toBe(DEFAULT);
    expect(term.screen.scrollback).toHaveLength(1);
  });

  it("fills the screen with E for the alignment test", () => {
    expect(rows(run("\x1b#8", 4, 2))).toEqual(["EEEE", "EEEE"]);
  });
});

describe("character sets", () => {
  it("draws lines with the DEC special graphics set", () => {
    expect(rows(run("\x1b(0lqqk\x1b(B"))[0]).toBe("┌──┐");
    expect(rows(run("\x1b(0x\x1b(Bx"))[0]).toBe("│x");
  });

  it("switches with shift out and shift in", () => {
    expect(rows(run("\x1b)0\x0eq\x0fq"))[0]).toBe("─q");
  });

  it("maps only the characters that have a graphic", () => {
    expect(translate("A", "0")).toBe("A");
    expect(translate("q", "0")).toBe("─");
    expect(translate("q", "B")).toBe("q");
  });
});

describe("what the terminal answers", () => {
  it("answers a status report with OK", () => {
    const { term, replies } = open();
    term.write("\x1b[5n");
    expect(replies).toEqual(["\x1b[0n"]);
  });

  it("answers a cursor position report, 1-based", () => {
    const { term, replies } = open();
    term.write("\x1b[3;7H\x1b[6n");
    expect(replies).toEqual(["\x1b[3;7R"]);
  });

  it("reports the position inside the region under origin mode", () => {
    const { term, replies } = open();
    term.write("\x1b[2;4r\x1b[?6h\x1b[2;2H\x1b[6n");
    expect(replies).toEqual(["\x1b[2;2R"]);
  });

  it("answers both device attribute queries", () => {
    const { term, replies } = open();
    term.write("\x1b[c\x1b[0c\x1b[>c");
    expect(replies).toEqual(["\x1b[?1;2c", "\x1b[?1;2c", "\x1b[>0;0;0c"]);
  });

  it("never answers a query for the window's title or size, which a file could have used to type into a shell", () => {
    const { term, replies } = open();
    term.write("\x1b[21t\x1b[20t\x1b[18t\x1b[14t\x1b]0;?\x07");
    expect(replies).toEqual([]);
  });

  it("answers only what it understands", () => {
    const { term, replies } = open();
    term.write("\x1b[99n\x1b[?99n\x1b[=c\x1b[>1c");
    expect(replies).toEqual([]);
  });
});

describe("titles and strings", () => {
  it("sets the title from OSC 0 and 2, ended by BEL or ST", () => {
    const { term, titles } = open();
    term.write("\x1b]0;first\x07\x1b]2;second\x1b\\");
    expect(titles).toEqual(["first", "second"]);
    expect(term.screen.title).toBe("second");
  });

  it("cleans a title of control characters and cuts it", () => {
    const { term } = open();
    term.write(`\x1b]0;a\x01b\x7fc${"x".repeat(500)}\x07`);
    expect(term.screen.title.startsWith("abc")).toBe(true);
    expect(term.screen.title.length).toBe(200);
  });

  it("does not obey the clipboard, hyperlink, or colour commands, and prints nothing for them", () => {
    const { term, titles, replies } = open();
    term.write("a\x1b]52;c;SGVsbG8=\x07b\x1b]8;;https://evil.example\x1b\\c\x1b]4;1;?\x07d\x1b]10;?\x07");
    expect(rows(term)[0]).toBe("abcd");
    expect(titles).toEqual([]);
    expect(replies).toEqual([]);
  });

  it("reads past device control strings and application commands without printing them", () => {
    expect(rows(run("a\x1bPsome device string\x1b\\b\x1b_apc\x1b\\c\x1b^pm\x1b\\d\x1bXsos\x1b\\e"))[0]).toBe("abcde");
  });

  it("gives up on a string that never ends, so it cannot swallow the screen", () => {
    const term = run(`\x1bP${"x".repeat(70_000)}after`);
    expect(term.screen.text().endsWith("after")).toBe(true);
  });

  it("cuts an operating system command at a limit", () => {
    const { term, titles } = open();
    term.write(`\x1b]0;${"y".repeat(10_000)}\x07`);
    expect(titles).toHaveLength(1);
    expect(titles[0]?.length).toBe(200);
  });
});

describe("a sequence split across writes", () => {
  it("is the same as one in one piece", () => {
    const whole = run("a\x1b[1;31mb\x1b[0mc\x1b]0;t\x07d");
    const { term } = open();
    for (const byte of "a\x1b[1;31mb\x1b[0mc\x1b]0;t\x07d") term.write(byte);
    expect(rows(term)).toEqual(rows(whole));
    expect(term.screen.title).toBe("t");
    expect(term.screen.row(0).fg[1]).toBe(1);
  });

  it("keeps a lone escape at the end of a write waiting for the next", () => {
    const { term } = open();
    term.write("a\x1b");
    term.write("[2Jb");
    expect(rows(term)[0]).toBe(" b");
  });
});

describe("garbage", () => {
  it("drops an unknown sequence whole, and goes on printing after it", () => {
    expect(rows(run("a\x1b[?9999;1;2zb\x1b[=7Xc\x1b[<1Ad"))[0]).toBe("abcd");
  });

  it("cancels a sequence on CAN and SUB", () => {
    expect(rows(run("a\x1b[3\x18b"))[0]).toBe("ab");
    expect(rows(run("a\x1b[3\x1ab"))[0]).toBe("ab");
  });

  it("restarts at an escape inside a sequence", () => {
    expect(rows(run("\x1b[3\x1b[2Jx"))[0]).toBe("x");
  });

  it("runs a control character inside a sequence without ending it", () => {
    expect(rows(run("a\x1b[3\nD"))[0]).toBe("a");
  });

  it("ignores a parameter marker in the middle of a sequence", () => {
    expect(rows(run("a\x1b[1?2Hb"))[0]).toBe("ab");
  });

  it("keeps at most 32 parameters and clamps a huge one", () => {
    const term = run(`\x1b[${"1;".repeat(100)}1mx\x1b[99999999C`, 100, 2);
    expect(term.screen.x).toBeLessThanOrEqual(99);
  });

  it("survives every byte value in every position without throwing", () => {
    const { term } = open(10, 4);
    let state = 12345;
    const next = (): number => {
      state = (state * 1103515245 + 12345) & 0x7fffffff;
      return state;
    };
    const alphabet = "\x1b[]()#;:?>=<0123456789 !\"$%&'*+,-./@ABCDEFGHIJKLMNOPQRSTUVWXYZ`abcdefghijklmnopqrstuvwxyz{|}~\x07\x08\x09\x0a\x0d\x18\x1a世́";
    for (let i = 0; i < 20_000; i++) term.write(alphabet.charAt(next() % alphabet.length));
    expect(term.screen.x).toBeGreaterThanOrEqual(0);
    expect(term.screen.x).toBeLessThan(10);
    expect(term.screen.y).toBeGreaterThanOrEqual(0);
    expect(term.screen.y).toBeLessThan(4);
  });
});

describe("resizing", () => {
  it("cuts and pads lines, and keeps the cursor on the screen", () => {
    const term = run("abcdefghij\x1b[1;10H", 10, 3);
    term.resize(5, 3);
    expect(rows(term)[0]).toBe("abcde");
    expect(term.screen.x).toBe(4);
    term.resize(8, 3);
    expect(rows(term)[0]).toBe("abcde");
  });

  it("pushes the top lines into the scrollback when it gets shorter than the cursor's line", () => {
    const term = run("1\r\n2\r\n3", 5, 3);
    term.resize(5, 2);
    expect(rows(term)).toEqual(["2", "3"]);
    expect(term.screen.scrollback.map((row) => row.text())).toEqual(["1"]);
    expect(term.screen.y).toBe(1);
  });

  it("brings lines back from the scrollback when it gets taller", () => {
    const term = run("1\n2\n3\n4", 5, 3);
    term.resize(5, 4);
    expect(rows(term)[0]).toBe("1");
    expect(term.screen.scrollback).toHaveLength(0);
  });

  it("cuts a wide character in two into a blank", () => {
    const term = run("ab世", 4, 2);
    term.resize(3, 2);
    expect(rows(term)[0]).toBe("ab");
  });

  it("resets the scroll region", () => {
    const term = run("\x1b[2;3r", 5, 5);
    term.resize(5, 8);
    expect([term.screen.scrollTop, term.screen.scrollBottom]).toEqual([0, 7]);
  });
});

describe("scrollback", () => {
  it("keeps at most its limit, and counts what it dropped so a selection can follow", () => {
    const term = new Terminal(5, 2, {}, 3);
    term.write("1\r\n2\r\n3\r\n4\r\n5\r\n6\r\n7");
    expect(term.screen.scrollback).toHaveLength(3);
    expect(term.screen.trimmed).toBe(2);
    expect(term.screen.text()).toBe("3\n4\n5\n6\n7");
  });
});

describe("paste", () => {
  it("sends a paste as it is, with line breaks as the Enter key sends them", () => {
    expect(open().term.paste("a\nb\r\nc")).toBe("a\rb\rc");
  });

  it("wraps it when the shell asked, removing any marker that would end it early", () => {
    const { term } = open();
    term.write("\x1b[?2004h");
    expect(term.paste("ls")).toBe("\x1b[200~ls\x1b[201~");
    expect(term.paste("echo \x1b[201~; rm -rf ~")).toBe("\x1b[200~echo ; rm -rf ~\x1b[201~");
  });
});
