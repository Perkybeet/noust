import { describe, expect, it } from "vitest";

import { cssRgb, fixedColour, isTrueColour, trueColour } from "./palette";
import { commandLine, parseCommand, quoteForShell, shellSyntaxIn } from "./command";
import { TRUE_COLOUR } from "./screen";

describe("a command in one line", () => {
  it("splits on spaces", () => {
    expect(parseCommand("rails runner  puts").argv).toEqual(["rails", "runner", "puts"]);
  });

  it("keeps spaces inside quotes, single or double", () => {
    expect(parseCommand(`rails runner 'puts "a b"'`).argv).toEqual(["rails", "runner", 'puts "a b"']);
    expect(parseCommand(`echo "two words" 'and three words'`).argv).toEqual(["echo", "two words", "and three words"]);
  });

  it("keeps a character after a backslash, and an escaped quote inside double quotes", () => {
    expect(parseCommand(String.raw`echo a\ b`).argv).toEqual(["echo", "a b"]);
    expect(parseCommand(String.raw`echo "say \"hi\" \\ done"`).argv).toEqual(["echo", String.raw`say "hi" \ done`]);
  });

  it("takes everything in single quotes literally, a backslash included", () => {
    expect(parseCommand(String.raw`echo 'a\b'`).argv).toEqual(["echo", String.raw`a\b`]);
  });

  it("makes an empty pair of quotes an empty argument", () => {
    expect(parseCommand(`cmd "" ''`).argv).toEqual(["cmd", "", ""]);
  });

  it("joins a quote to the text beside it, as a shell does", () => {
    expect(parseCommand(`echo a"b c"d`).argv).toEqual(["echo", "ab cd"]);
  });

  it("says when a quote is left open", () => {
    expect(parseCommand(`echo "unfinished`).unclosed).toBe('"');
    expect(parseCommand(`echo 'unfinished`).unclosed).toBe("'");
    expect(parseCommand("echo done").unclosed).toBeNull();
  });

  it("gives nothing for a line that is blank", () => {
    expect(parseCommand("   ").argv).toEqual([]);
    expect(parseCommand("").argv).toEqual([]);
  });

  it("does not expand anything a shell would", () => {
    expect(parseCommand("echo $HOME ~ * `id` $(id) a|b a;b a&&b >out").argv).toEqual([
      "echo",
      "$HOME",
      "~",
      "*",
      "`id`",
      "$(id)",
      "a|b",
      "a;b",
      "a&&b",
      ">out",
    ]);
  });

  it("finds the arguments a shell would have acted on, to say they are passed as they are", () => {
    expect(shellSyntaxIn(["ls", "-la"])).toEqual([]);
    expect(shellSyntaxIn(["echo", "$HOME", "a|b", "plain", "*.js"])).toEqual(["$HOME", "a|b", "*.js"]);
  });
});

describe("a command written for a shell", () => {
  it("leaves plain words as they are", () => {
    expect(commandLine(["rails", "runner", "--env=production", "/srv/app/x.rb"])).toBe("rails runner --env=production /srv/app/x.rb");
  });

  it("quotes what a shell would act on, so the line reads back as the same arguments", () => {
    expect(quoteForShell("puts User.count")).toBe("'puts User.count'");
    expect(quoteForShell("a|b")).toBe("'a|b'");
    expect(quoteForShell("$HOME")).toBe("'$HOME'");
    expect(quoteForShell("")).toBe("''");
  });

  it("writes a single quote so that it survives", () => {
    expect(quoteForShell("it's")).toBe("'it'\\''s'");
  });

  it("is undone by the parser the page uses, for the arguments it can write", () => {
    const argv = ["echo", "two words", "it's", "a|b", ""];
    expect(parseCommand(commandLine(argv)).argv).toEqual(argv);
  });
});

describe("the fixed colours", () => {
  it("builds the cube from its six levels", () => {
    expect(fixedColour(16)).toEqual([0, 0, 0]);
    expect(fixedColour(17)).toEqual([0, 0, 95]);
    expect(fixedColour(21)).toEqual([0, 0, 255]);
    expect(fixedColour(196)).toEqual([255, 0, 0]);
    expect(fixedColour(231)).toEqual([255, 255, 255]);
    expect(fixedColour(46)).toEqual([0, 255, 0]);
  });

  it("builds the greys from 8 in steps of 10", () => {
    expect(fixedColour(232)).toEqual([8, 8, 8]);
    expect(fixedColour(255)).toEqual([238, 238, 238]);
  });

  it("tells a true colour from a palette index and from the default", () => {
    expect(isTrueColour(TRUE_COLOUR | 0x336699)).toBe(true);
    expect(trueColour(TRUE_COLOUR | 0x336699)).toEqual([0x33, 0x66, 0x99]);
    expect(isTrueColour(196)).toBe(false);
    expect(isTrueColour(-1)).toBe(false);
  });

  it("writes a CSS colour", () => {
    expect(cssRgb(1, 2, 3)).toBe("rgb(1 2 3)");
  });
});
