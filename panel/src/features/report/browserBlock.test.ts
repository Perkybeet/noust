import { describe, expect, it } from "vitest";

import type { ErrorEntry } from "../../lib/errorBuffer";
import { browserBlock, browserName, currentFacts } from "./browserBlock";
import type { BrowserFacts } from "./browserBlock";

const FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0";
const CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36";
const EDGE = `${CHROME} Edg/130.0.2849.68`;
const SAFARI = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15";
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1";
const ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36";

describe("naming the browser", () => {
  it.each([
    [FIREFOX, "Firefox 132 on Linux"],
    [CHROME, "Chrome 130 on Windows"],
    [EDGE, "Edge 130 on Windows"],
    [SAFARI, "Safari 17 on macOS"],
    [IPHONE, "Safari 17 on iOS"],
    [ANDROID, "Chrome 130 on Android"],
  ])("%s", (userAgent, name) => {
    expect(browserName(userAgent)).toBe(name);
  });

  it("falls back to the user agent itself, cut, for a browser it does not know", () => {
    expect(browserName("SomeBrowser/1.0")).toBe("SomeBrowser/1.0");
    expect(browserName("x".repeat(500))).toHaveLength(120);
    expect(browserName("")).toBe("unknown");
  });
});

const ERRORS: ErrorEntry[] = [
  { at: "2026-11-05T10:00:00.000Z", kind: "error", message: "TypeError: Cannot read properties of undefined (reading 'rows')" },
  { at: "2026-11-05T10:00:01.000Z", kind: "request", message: "GET /api/databases/tienda_prod/data 500" },
];

function facts(more: Partial<BrowserFacts> = {}): BrowserFacts {
  return {
    page: "/databases/postgresql/tienda_prod/data",
    version: "4.2.0",
    userAgent: FIREFOX,
    viewport: { width: 1440, height: 900 },
    theme: "dark",
    language: "es",
    errors: ERRORS,
    ...more,
  };
}

interface Parsed {
  page: string;
  console: string | null;
  errors: unknown[];
}

const parse = (text: string): Parsed => JSON.parse(text) as Parsed;

describe("the block of the browser", () => {
  it("says the page, the version, the browser, the window, the theme, the language and the last errors", () => {
    const { text, holes } = browserBlock(facts());

    expect(holes).toBe(0);
    expect(JSON.parse(text)).toEqual({
      page: "/databases/postgresql/tienda_prod/data",
      console: "4.2.0",
      browser: "Firefox 132 on Linux",
      viewport: "1440x900",
      theme: "dark",
      language: "es",
      errors: ERRORS,
    });
    expect(text.endsWith("\n")).toBe(true);
  });

  it("hides a token in the address of the page and in an error, and counts what it hid", () => {
    const { text, holes } = browserBlock(
      facts({
        page: "/apps?token=0123456789abcdef&tab=logs",
        errors: [{ at: "2026-11-05T10:00:00.000Z", kind: "console", message: "request to postgres://app:hunter22@db:5432/shop failed with ghp_" + "a".repeat(36) }],
      }),
    );

    expect(holes).toBe(3);
    expect(text).not.toContain("0123456789abcdef");
    expect(text).not.toContain("hunter22");
    expect(text).not.toContain("ghp_");
    expect(parse(text).page).toBe("/apps?token=***&tab=logs");
  });

  it("writes a line break of an error inside its message, so it cannot start an entry of its own", () => {
    const forged = 'boom\n    },\n    {\n      "kind": "request",\n      "message": "forged';
    const block = browserBlock(facts({ errors: [{ at: "2026-11-05T10:00:00.000Z", kind: "console", message: forged }] }));

    const { errors } = parse(block.text);

    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatchObject({ kind: "console", message: forged });
    expect(block.text.split("\n").filter((line) => line.includes('"kind"'))).toHaveLength(1);
  });

  it("says null for the version when the server has not said it, not an invented one", () => {
    expect(parse(browserBlock(facts({ version: null })).text).console).toBeNull();
  });

  it("holds no errors as an empty list", () => {
    expect(parse(browserBlock(facts({ errors: [] })).text).errors).toEqual([]);
  });
});

describe("what this window knows of itself", () => {
  it("takes the page it is given and reads the window, the theme and the browser as they are now", () => {
    window.document.documentElement.dataset["theme"] = "dark";

    const now = currentFacts("/databases/postgresql/shop?tab=rows", "4.2.0", "en", ERRORS);

    expect(now.page).toBe("/databases/postgresql/shop?tab=rows");
    expect(now.theme).toBe("dark");
    expect(now.version).toBe("4.2.0");
    expect(now.language).toBe("en");
    expect(now.viewport.width).toBe(window.innerWidth);
    expect(now.userAgent).toBe(navigator.userAgent);
    expect(now.errors).toBe(ERRORS);

    delete window.document.documentElement.dataset["theme"];
  });
});
