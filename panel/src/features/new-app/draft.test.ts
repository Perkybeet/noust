import { afterEach, describe, expect, it } from "vitest";

import { clearDraft, hasDraft, readDraft, saveDraft } from "./draft";

afterEach(() => {
  window.sessionStorage.clear();
});

describe("the wizard's draft", () => {
  it("keeps the way in, the source, the branch and the step, and nothing else", () => {
    expect(hasDraft()).toBe(false);
    saveDraft({ mode: "github", source: "github:acme/api", branch: "main" });
    expect(readDraft()).toEqual({ mode: "github", source: "github:acme/api", branch: "main", step: "source" });
    expect(Object.keys(JSON.parse(window.sessionStorage.getItem("noust.newApp.draft") ?? "{}") as object).sort()).toEqual(["branch", "mode", "source", "step"]);
    expect(hasDraft()).toBe(true);
  });

  it("is read without being removed, so rendering twice finds it twice", () => {
    saveDraft({ mode: "manual", source: "https://github.com/acme/api.git", branch: "" });
    expect(readDraft()).not.toBeNull();
    expect(readDraft()).not.toBeNull();
    clearDraft();
    expect(readDraft()).toBeNull();
  });

  it("never carries more than an address's worth of text", () => {
    saveDraft({ mode: "manual", source: "x".repeat(10_000), branch: "y".repeat(10_000) });
    expect(readDraft()?.source).toHaveLength(2_000);
    expect(readDraft()?.branch).toHaveLength(2_000);
  });

  it("is not fooled by what is not a draft", () => {
    for (const raw of ["", "not json", "null", "[]", '{"mode":"nonsense","source":"","branch":""}', '{"mode":"github","source":1,"branch":""}', '{"mode":"github","source":""}']) {
      window.sessionStorage.setItem("noust.newApp.draft", raw);
      expect(readDraft(), raw).toBeNull();
    }
  });
});
