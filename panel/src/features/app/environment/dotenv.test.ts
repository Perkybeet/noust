import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import {
  commentProblem,
  nameProblem,
  parseDotenv,
  pythonLines,
  pythonStrip,
  valueProblem,
} from "./dotenv";

// The fixtures the Python suite (tests/test_env_fixtures.py) parses with EnvManager, read with
// Node's own fs (they sit outside the panel, where Vite's module loader may not reach) and
// decoded as UTF-8 with every byte kept: the CRLF and BOM cases are the point. The app's
// tsconfig has no Node types, so the little of fs used here is typed locally.
interface NodeFs {
  readdirSync: (path: string) => string[];
  readFileSync: (path: string, encoding: "utf8") => string;
}
const NODE_FS = "node:fs";
const fs = (await import(/* @vite-ignore */ NODE_FS)) as NodeFs;
// Vitest runs from panel/; the fixtures are the repository's.
const { process } = globalThis as unknown as { process: { cwd: () => string } };
const DIR = `${process.cwd()}/../tests/fixtures/env/`;

const CASES = fs
  .readdirSync(DIR)
  .filter((file) => file.endsWith(".env"))
  .sort()
  .map((file) => {
    const name = file.replace(/\.env$/, "");
    const text = fs.readFileSync(DIR + file, "utf8");
    const expected = JSON.parse(fs.readFileSync(`${DIR}${name}.json`, "utf8")) as Record<string, string>;
    return { name, text, expected };
  });

const COMMENT_CASES = fs
  .readdirSync(DIR)
  .filter((file) => file.endsWith(".comments.json"))
  .sort()
  .map((file) => {
    const name = file.replace(/\.comments\.json$/, "");
    return {
      name,
      text: fs.readFileSync(`${DIR}${name}.env`, "utf8"),
      expected: JSON.parse(fs.readFileSync(DIR + file, "utf8")) as Record<string, string>,
    };
  });

describe("the shared .env fixtures", () => {
  it("are all found, each with its expected map", () => {
    expect(CASES.length).toBeGreaterThanOrEqual(9);
    for (const { name, expected } of CASES) expect(expected, `${name}.json`).toBeDefined();
  });

  it("keep their CRLF bytes", () => {
    const crlf = CASES.find((c) => c.name === "crlf");
    expect(crlf?.text).toContain("\r\n");
  });

  it.each(CASES)("parse $name exactly as EnvManager does", ({ text, expected }) => {
    expect(Object.fromEntries(parseDotenv(text).variables)).toEqual(expected);
  });

  it.each(COMMENT_CASES)("read the comments of $name exactly as EnvManager does", ({ text, expected }) => {
    expect(Object.fromEntries(parseDotenv(text).comments)).toEqual(expected);
  });
});

describe("a value of several lines", () => {
  const parse = (text: string) => Object.fromEntries(parseDotenv(text).variables);

  it("goes on until a line that ends with the quote that opened it", () => {
    expect(parse("KEY='a\nb\nc'\nNEXT=1\n")).toEqual({ KEY: "a\nb\nc", NEXT: "1" });
    expect(parse('JSON="{\n  \\"a\\": 1\n}"\n')).toEqual({ JSON: '{\n  \\"a\\": 1\n}' });
  });

  it("keeps the indentation, the blank lines and the spaces at the end of the first line", () => {
    expect(parse("K='  a   \n\n    b  '\n")).toEqual({ K: "  a   \n\n    b  " });
  });

  it("does not take the lines of the value for assignments or comments", () => {
    expect(parse("KEY='a\nINJECTED=1\n# not a comment\nZGVm=='\nAFTER=2\n")).toEqual({ KEY: "a\nINJECTED=1\n# not a comment\nZGVm==", AFTER: "2" });
  });

  it("reads windows line endings as line feeds", () => {
    expect(parse("K='a\r\nb'\r\nN=1\r\n")).toEqual({ K: "a\nb", N: "1" });
  });

  it("is the line as written when no later line closes the quote", () => {
    expect(parse('A="never closed\nB=1\n')).toEqual({ A: '"never closed', B: "1" });
    expect(parse("A=\"open\nB='x'\n")).toEqual({ A: '"open', B: "x" });
  });

  it("counts the line it starts on, and the next assignment after it", () => {
    const { assignments } = parseDotenv("A='x\ny'\nB=2\n");
    expect(assignments.map((a) => [a.name, a.line])).toEqual([
      ["A", 1],
      ["B", 3],
    ]);
  });
});

describe("the comment above a variable", () => {
  it("is the run of # lines directly above it", () => {
    const { comments } = parseDotenv("# Where the queue lives.\n# Change it with care.\nQUEUE=redis\nPLAIN=1\n");
    expect(Object.fromEntries(comments)).toEqual({ QUEUE: "Where the queue lives.\nChange it with care." });
  });

  it("is cut off by a blank line, and belongs to the next variable only", () => {
    const { comments } = parseDotenv("# stray\n\nA=1\n# mine\nB=2\nC=3\n");
    expect(Object.fromEntries(comments)).toEqual({ B: "mine" });
  });

  it("is on the assignment too", () => {
    expect(parseDotenv("# one\nA=1\n").assignments[0]?.comment).toBe("one");
  });
});

describe("what the API accepts as a value of lines", () => {
  it("lets a line feed through and nothing else that is a control character", () => {
    expect(valueProblem("a\nb")).toBeNull();
    expect(valueProblem("a\r\nb")).toMatch(/carriage return/);
    expect(valueProblem("a\nb\tc")).toMatch(/a tab/);
    expect(valueProblem("a\nb\u2028c")).toMatch(/U\+2028/);
    expect(valueProblem("a\x85b")).toMatch(/U\+0085/);
  });

  it("refuses a single quote beside a line feed, in both languages", () => {
    expect(valueProblem("it's\nbroken")).toMatch(/several lines.*single quotes/);
    expect(valueProblem("it's fine")).toBeNull();
  });

  it("judges a comment as plain short text", () => {
    expect(commentProblem("what it is for")).toBeNull();
    expect(commentProblem("x".repeat(501))).toMatch(/500 characters/);
    expect(commentProblem("l\n".repeat(9))).toMatch(/8 lines/);
    expect(commentProblem("a\tb")).toMatch(/plain text/);
  });
});

describe("parseDotenv", () => {
  it("keeps the first position of a name and the last value", () => {
    const { variables, assignments } = parseDotenv("A=1\nB=2\nA=3\n");
    expect([...variables]).toEqual([
      ["A", "3"],
      ["B", "2"],
    ]);
    expect(assignments.map((a) => a.line)).toEqual([1, 2, 3]);
  });

  it("reports lines that are neither comments nor assignments", () => {
    expect(parseDotenv("# ok\nnot an assignment\nA=1").skipped).toEqual([{ line: 2, name: "not an assignment", value: "", comment: "" }]);
  });

  it("drops a leading export keyword from the name, as a shell would", () => {
    expect([...parseDotenv("export FOO=bar\nexport\t\tBAR=1\nexported=yes\nEXPORT_DIR=/x\nexport=2").variables.keys()]).toEqual([
      "FOO",
      "BAR",
      "exported",
      "EXPORT_DIR",
      "export",
    ]);
  });
});

describe("Python's string rules", () => {
  it("strips what str.strip() strips, not what trim() does", () => {
    expect(pythonStrip("\x1f a \x85")).toBe("a");
    expect(pythonStrip("\ufeffa")).toBe("\ufeffa");
  });

  it("splits lines like str.splitlines() after universal newlines", () => {
    expect(pythonLines("a\r\nb\rc\x0bd\u2028e\n")).toEqual(["a", "b", "c", "d", "e"]);
    expect(pythonLines("")).toEqual([]);
    expect(pythonLines("a\n\n")).toEqual(["a", ""]);
  });
});

describe("what the API accepts", () => {
  it("names a bad name and says how to fix it", () => {
    expect(nameProblem("API_URL")).toBeNull();
    expect(nameProblem("MY VAR")).toMatch(/is not a variable name/);
    expect(nameProblem("")).toMatch(/no name/);
    expect(nameProblem("2FA")).toMatch(/start with a letter/);
  });

  it("refuses control characters in values, naming them", () => {
    expect(valueProblem("caf\xe9")).toBeNull();
    expect(valueProblem("a\tb")).toMatch(/a tab/);
    expect(valueProblem("a\x7fb")).toMatch(/U\+007F/);
  });
});

describe("in Spanish", () => {
  it("names the same problems, in Spanish", async () => {
    await setLocale("es");
    expect(nameProblem("MY VAR", "es")).toMatch(/no es un nombre de variable válido/);
    expect(nameProblem("", "es")).toMatch(/ningún nombre/);
    expect(valueProblem("a\tb", "es")).toMatch(/un tabulador/);
    expect(valueProblem("a\x7fb", "es")).toMatch(/U\+007F/);
    expect(valueProblem("it's\nroto", "es")).toMatch(/varias líneas/);
    expect(commentProblem("x".repeat(501), "es")).toMatch(/500 caracteres/);
  });
});
