// The budget check is a gate in CI, so it is tested against a build it makes up: random bytes do not
// compress, which makes each file weigh what it is told to.

import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

const script = path.join(path.dirname(fileURLToPath(import.meta.url)), "check-bundle-budget.mjs");
const KB = 1024;
/** @type {string[]} */
const made = [];

/**
 * A build whose entry is 10 kB, whose one preloaded chunk is 20 kB, whose lazy chunk is 40 kB, whose stylesheet is 5 kB,
 * and whose texts are one English chunk of 6 kB and two Spanish ones of 7 and 8 kB (named as vite.config.ts names them),
 * and two languages contributed as data: French in one file of 60 kB, Brazilian Portuguese in one of 45 kB.
 *
 * @param {{ index?: boolean }} [options]
 */
function build({ index = true } = {}) {
  const dir = mkdtempSync(path.join(tmpdir(), "noust-budget-"));
  made.push(dir);
  mkdirSync(path.join(dir, "assets"));
  /**
   * @param {string} name
   * @param {number} kb
   */
  const file = (name, kb) => {
    writeFileSync(path.join(dir, "assets", name), randomBytes(kb * KB));
  };
  file("index.js", 10);
  file("shared.js", 20);
  file("page.js", 40);
  file("i18n-en-nav-AbCd1234.js", 6);
  file("i18n-es-nav-AbCd1234.js", 7);
  file("i18n-es-servers-AbCd1234.js", 8);
  // Vite's hashes may hold a dash: the code ends at the dot.
  file("i18n-community-fr.Ab-Cd123.js", 60);
  file("i18n-community-pt-BR.Ef_Gh456.js", 45);
  // A feature that is called like a catalog is not one.
  file("nav-AbCd1234.js", 3);
  file("app.css", 5);
  if (index) {
    writeFileSync(
      path.join(dir, "index.html"),
      [
        "<!doctype html><html><head>",
        '<script type="module" crossorigin src="/assets/index.js"></script>',
        '<link rel="modulepreload" crossorigin href="/assets/shared.js">',
        '<link rel="stylesheet" crossorigin href="/assets/app.css">',
        "</head><body></body></html>",
      ].join("\n"),
    );
  }
  return dir;
}

/**
 * @param {string} dir
 * @param {Record<string, number>} budget
 */
function check(dir, budget) {
  const file = path.join(dir, "budget.json");
  writeFileSync(file, JSON.stringify(budget));
  const result = spawnSync(process.execPath, [script, dir, file], { encoding: "utf8" });
  return { status: result.status, out: result.stdout, err: result.stderr };
}

const ROOMY = { initialJs: 40 * KB, initialCss: 8 * KB, chunk: 50 * KB, totalJs: 150 * KB, catalogEn: 10 * KB, catalogEs: 20 * KB, catalogCommunity: 80 * KB };

afterEach(() => {
  for (const dir of made.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe("the bundle budget", () => {
  it("passes a build within every number, and says how close it is", () => {
    const result = check(build(), ROOMY);

    expect(result.status).toBe(0);
    expect(result.out).toContain("within its budget");
    // The entry and its preload are 30 kB, not the 70 kB of every chunk: a lazy chunk is not initial.
    expect(result.out).toMatch(/initialJs\s+30\.\d kB of 40\.0 kB/);
    expect(result.out).toMatch(/totalJs\s+94\.\d kB of 150\.0 kB/);
    // The texts are weighed by language, and by the name the build gives them.
    expect(result.out).toMatch(/catalogEn\s+6\.\d kB of 10\.0 kB/);
    expect(result.out).toMatch(/catalogEs\s+15\.\d kB of 20\.0 kB/);
  });

  it("fails a build whose first load is heavier than its budget, naming the number", () => {
    const result = check(build(), { ...ROOMY, initialJs: 25 * KB });

    expect(result.status).toBe(1);
    expect(result.err).toContain("initialJs is 30.");
    expect(result.err).toContain("over its budget of 25.0 kB");
    expect(result.err).not.toContain("totalJs is");
  });

  it("fails on the largest chunk, and names the file", () => {
    const result = check(build(), { ...ROOMY, chunk: 30 * KB });

    expect(result.status).toBe(1);
    expect(result.out).toContain("page.js");
    expect(result.err).toContain("chunk is 40.");
  });

  it("fails on the stylesheet and on the total", () => {
    const result = check(build(), { ...ROOMY, initialCss: 4 * KB, totalJs: 60 * KB });

    expect(result.status).toBe(1);
    expect(result.err).toContain("initialCss is 5.");
    expect(result.err).toContain("totalJs is 94.");
  });

  it("fails on the texts of one language alone, and not on the other", () => {
    const spanish = check(build(), { ...ROOMY, catalogEs: 12 * KB });

    expect(spanish.status).toBe(1);
    expect(spanish.err).toContain("catalogEs is 15.");
    expect(spanish.err).not.toContain("catalogEn is");
    expect(spanish.err).not.toContain("totalJs is");

    const english = check(build(), { ...ROOMY, catalogEn: 5 * KB });

    expect(english.status).toBe(1);
    expect(english.err).toContain("catalogEn is 6.");
    expect(english.err).not.toContain("catalogEs is");
  });

  it("weighs a contributed language on its own, by its heaviest, and leaves it out of the chunk and the total", () => {
    const result = check(build(), ROOMY);

    expect(result.status).toBe(0);
    // French is 60 kB, more than the 50 kB a chunk may weigh, and is not the largest chunk: only who chose it fetches it.
    expect(result.out).toMatch(/catalogCommunity\s+60\.\d kB of 80\.0 kB\s+\(fr\)/);
    expect(result.out).toContain("page.js");

    const tight = check(build(), { ...ROOMY, catalogCommunity: 50 * KB });

    expect(tight.status).toBe(1);
    expect(tight.err).toContain("catalogCommunity is 60.");
    expect(tight.err).not.toContain("totalJs is");
    expect(tight.err).not.toContain("chunk is");
  });

  it("refuses a directory that is not a build", () => {
    const result = check(build({ index: false }), ROOMY);

    expect(result.status).toBe(1);
    expect(result.err).toContain("does not exist");
  });

  it("refuses a budget with a number missing rather than passing on it", () => {
    const partial = { initialJs: ROOMY.initialJs, initialCss: ROOMY.initialCss, chunk: ROOMY.chunk };

    const result = check(build(), partial);

    expect(result.status).toBe(1);
    expect(result.err).toContain('no number for "totalJs"');

    const noTexts = check(build(), { initialJs: ROOMY.initialJs, initialCss: ROOMY.initialCss, chunk: ROOMY.chunk, totalJs: ROOMY.totalJs, catalogEn: ROOMY.catalogEn });

    expect(noTexts.status).toBe(1);
    expect(noTexts.err).toContain('no number for "catalogEs"');
  });
});
