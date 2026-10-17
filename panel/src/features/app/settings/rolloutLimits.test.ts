import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { DEFAULT_SIGNAL, parseRolloutDraft, rolloutChanges, rolloutDraftChanges, rolloutDraftOf } from "./rolloutLimits";

describe("the delivery form", () => {
  it("reads what is stored, and takes an explicit SIGTERM for the default it is", () => {
    expect(rolloutDraftOf({ history_keep: null, stop_timeout: null, stop_signal: null })).toEqual({ history_keep: "", stop_timeout: "", stop_signal: DEFAULT_SIGNAL });
    expect(rolloutDraftOf({ history_keep: 40, stop_timeout: 300, stop_signal: "SIGINT" })).toEqual({ history_keep: "40", stop_timeout: "300", stop_signal: "SIGINT" });
    expect(rolloutDraftOf({ history_keep: null, stop_timeout: null, stop_signal: "SIGTERM" }).stop_signal).toBe(DEFAULT_SIGNAL);
  });

  it("reads an empty field as the default and a number as itself", () => {
    expect(parseRolloutDraft({ history_keep: "", stop_timeout: "", stop_signal: DEFAULT_SIGNAL }, "en")).toEqual({
      values: { history_keep: null, stop_timeout: null, stop_signal: null },
      errors: {},
    });
    expect(parseRolloutDraft({ history_keep: " 50 ", stop_timeout: "30", stop_signal: "SIGHUP" }, "en").values).toEqual({
      history_keep: 50,
      stop_timeout: 30,
      stop_signal: "SIGHUP",
    });
  });

  it("refuses a number out of range or not a number, saying the range", () => {
    for (const bad of ["0", "1001", "-3", "2.5", "many"]) {
      expect(parseRolloutDraft({ history_keep: bad, stop_timeout: "", stop_signal: DEFAULT_SIGNAL }, "en").errors.history_keep).toBe("A number from 1 to 1000, or empty for the server's.");
    }
    for (const bad of ["0", "3601", "1e3"]) {
      expect(parseRolloutDraft({ history_keep: "", stop_timeout: bad, stop_signal: DEFAULT_SIGNAL }, "en").errors.stop_timeout).toBe("A number of seconds from 1 to 3600, or empty.");
    }
    expect(parseRolloutDraft({ history_keep: "0", stop_timeout: "", stop_signal: DEFAULT_SIGNAL }, "en").values).toBeNull();
    // The bounds themselves are fine.
    expect(parseRolloutDraft({ history_keep: "1000", stop_timeout: "3600", stop_signal: DEFAULT_SIGNAL }, "en").values).not.toBeNull();
  });

  it("speaks Spanish when asked to", async () => {
    await setLocale("es");
    expect(parseRolloutDraft({ history_keep: "0", stop_timeout: "", stop_signal: DEFAULT_SIGNAL }, "es").errors.history_keep).toBe("Un número de 1 a 1000, o vacío para el del servidor.");
  });

  it("sends only the fields that changed", () => {
    const stored = { history_keep: 40, stop_timeout: null, stop_signal: null };
    expect(rolloutChanges({ history_keep: 40, stop_timeout: null, stop_signal: null }, stored)).toEqual({});
    expect(rolloutChanges({ history_keep: null, stop_timeout: null, stop_signal: null }, stored)).toEqual({ history_keep: null });
    expect(rolloutChanges({ history_keep: 40, stop_timeout: 30, stop_signal: "SIGINT" }, stored)).toEqual({ stop_timeout: 30, stop_signal: "SIGINT" });
  });

  it("counts the fields that differ, ignoring spaces", () => {
    const stored = { history_keep: "40", stop_timeout: "", stop_signal: DEFAULT_SIGNAL };
    expect(rolloutDraftChanges({ ...stored, history_keep: " 40 " }, stored)).toBe(0);
    expect(rolloutDraftChanges({ history_keep: "41", stop_timeout: "30", stop_signal: DEFAULT_SIGNAL }, stored)).toBe(2);
  });
});
