import { beforeAll, describe, expect, it } from "vitest";

import { bindT, loadCatalog } from "../../i18n";
import { outputLines } from "./output";
import { clock, endedSentence, secondsUntil } from "./words";

describe("outputLines", () => {
  it("strips the queue's stamp and level and keeps what the program printed", () => {
    const content = "[2026-10-06 09:00:01] [INFO] 42 users\n[2026-10-06 09:00:02] [INFO]   indented\n";
    expect(outputLines(content)).toEqual([
      { id: 0, text: "42 users" },
      { id: 1, text: "  indented" },
    ]);
  });

  it("keeps the level of a line that means trouble", () => {
    const content = "[2026-10-06 09:00:01] [ERROR] boom\n[2026-10-06 09:00:01] [WARNING] hmm\n[2026-10-06 09:00:01] [DEBUG] d\n";
    expect(outputLines(content).map((line) => line.level)).toEqual(["error", "warn", "debug"]);
  });

  it("leaves a line without the stamp as it is", () => {
    expect(outputLines("plain\n[not a stamp] text")).toEqual([
      { id: 0, text: "plain" },
      { id: 1, text: "[not a stamp] text" },
    ]);
  });

  it("keeps blank lines the program printed", () => {
    expect(outputLines("[2026-10-06 09:00:01] [INFO] a\n[2026-10-06 09:00:01] [INFO] \n[2026-10-06 09:00:01] [INFO] b\n").map((line) => line.text)).toEqual([
      "a",
      "",
      "b",
    ]);
  });

  it("has no lines for an empty log", () => {
    expect(outputLines("")).toEqual([]);
  });
});

describe("the clock", () => {
  it("counts down in the form a person reads at a glance", () => {
    expect(clock(42)).toBe("0:42");
    expect(clock(723)).toBe("12:03");
    expect(clock(3909)).toBe("1:05:09");
    expect(clock(-5)).toBe("0:00");
  });

  it("never counts below zero", () => {
    expect(secondsUntil(1_000, 5_000)).toBe(0);
    expect(secondsUntil(10_500, 10_000)).toBe(1);
  });
});

describe("endedSentence", () => {
  const t = bindT("en");
  beforeAll(async () => {
    await loadCatalog("es");
  });

  it("says why in a sentence, and says the idle limit in minutes", () => {
    expect(endedSentence(t, { reason: "idle", exitCode: null }, 600)).toBe("Closed: nothing was typed for 10 min.");
    expect(endedSentence(t, { reason: "limit", exitCode: null }, 600)).toBe("Closed: the session reached its time limit.");
    expect(endedSentence(t, { reason: "killed", exitCode: null }, 600)).toBe("An administrator ended this session.");
  });

  it("says the shell's exit status only when it is not a clean one", () => {
    expect(endedSentence(t, { reason: "exited", exitCode: 0 }, 600)).toBe("The shell exited.");
    expect(endedSentence(t, { reason: "exited", exitCode: null }, 600)).toBe("The shell exited.");
    expect(endedSentence(t, { reason: "exited", exitCode: 130 }, 600)).toBe("The shell exited with status 130.");
  });

  it("does not guess at a reason it does not know", () => {
    expect(endedSentence(t, { reason: "something-new", exitCode: null }, 600)).toBe("The session ended.");
  });

  it("speaks Spanish too", () => {
    expect(endedSentence(bindT("es"), { reason: "disconnected", exitCode: null }, 600)).toBe("Cerrada: la conexión estuvo caída más de un minuto.");
  });
});
