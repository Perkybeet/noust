import { describe, expect, it } from "vitest";

import { ORIGINS, modeOfOrigin, originOf } from "./origins";
import type { SourceMode } from "./SourceStep";

const MODES: readonly SourceMode[] = ["github", "gitlab", "manual", "image", "compose", "files", "recipe", "import", "adopt"];

describe("where an application comes from", () => {
  it("has five ways, and every mode of the wizard is under one of them", () => {
    expect(ORIGINS).toEqual(["repo", "catalog", "image", "files", "existing"]);
    for (const mode of MODES) expect(ORIGINS, mode).toContain(originOf(mode));
  });

  it("puts adopting and importing under the same card, so a stack that runs and an export are one way to start", () => {
    expect(originOf("adopt")).toBe("existing");
    expect(originOf("import")).toBe("existing");
  });

  it("lights the files card for an archive that was uploaded, whatever mode the upload left the wizard in", () => {
    expect(originOf("manual", "upload:0123456789abcdef0123456789abcdef")).toBe("files");
    expect(originOf("manual", "https://github.com/example/app.git")).toBe("repo");
    expect(originOf("manual", "upload:short")).toBe("repo");
  });

  it("starts each card in a mode that is under it", () => {
    for (const origin of ORIGINS) {
      for (const connected of [true, false]) expect(originOf(modeOfOrigin(origin, connected)), `${origin}, GitHub ${String(connected)}`).toBe(origin);
    }
  });

  it("opens the repository card on GitHub's list only where GitHub is connected", () => {
    expect(modeOfOrigin("repo", true)).toBe("github");
    expect(modeOfOrigin("repo", false)).toBe("manual");
  });
});
