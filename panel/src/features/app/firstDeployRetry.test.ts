import { describe, expect, it } from "vitest";

import type { FirstDeploy } from "../../api/queries/firstDeploys";
import { retryOf, sourceWasMasked } from "./firstDeployRetry";

const ATTEMPT: FirstDeploy = {
  job_id: "aa11",
  domain: "shop.example.com",
  origin: "source",
  source: "https://github.com/acme/shop.git",
  branch: "main",
  app_type: "nextjs",
  recipe: null,
  created_at: "2026-11-10T10:00:00+00:00",
  error: "Release r1 did not pass its health check",
  finished_at: "2026-11-10T10:05:00",
};

describe("trying a failed first deploy again", () => {
  it("opens the wizard on the source, the branch and the domain it was asked with", () => {
    expect(retryOf(ATTEMPT)).toEqual({
      draft: { mode: "manual", source: "https://github.com/acme/shop.git", branch: "main", domain: "shop.example.com" },
      search: { resume: 1 },
    });
  });

  it("opens an image as an image, with no branch", () => {
    expect(retryOf({ ...ATTEMPT, origin: "image", source: "nginx:1.27", branch: null })).toEqual({
      draft: { mode: "image", source: "nginx:1.27", branch: "", domain: "shop.example.com" },
      search: { resume: 1 },
    });
  });

  it("opens a recipe on its own install form, which is where its domain is asked", () => {
    expect(retryOf({ ...ATTEMPT, origin: "recipe", source: null, branch: null, recipe: "uptime-kuma" })).toEqual({
      draft: { mode: "recipe", source: "", branch: "", domain: "shop.example.com" },
      search: { start: "recipe", recipe: "uptime-kuma", resume: 1 },
    });
  });

  it("opens a typed Compose document on the Compose way in, empty: the document is not kept", () => {
    expect(retryOf({ ...ATTEMPT, origin: "compose", source: null, branch: null })).toEqual({
      draft: { mode: "compose", source: "", branch: "", domain: "shop.example.com" },
      search: { resume: 1 },
    });
  });

  it("opens the wizard empty for what it does not know", () => {
    expect(retryOf({ ...ATTEMPT, origin: "telepathy", source: null }).draft.mode).toBe("manual");
  });

  it("says when the address had a credential that was masked, so the operator types it again", () => {
    expect(sourceWasMasked(ATTEMPT)).toBe(false);
    expect(sourceWasMasked({ ...ATTEMPT, source: "https://***@github.com/acme/shop.git" })).toBe(true);
    expect(sourceWasMasked({ ...ATTEMPT, source: "https://ana:***@git.example.com/a.git" })).toBe(true);
    expect(sourceWasMasked({ ...ATTEMPT, source: "https://host.example.com/a.zip?token=***" })).toBe(true);
    expect(sourceWasMasked({ ...ATTEMPT, source: null })).toBe(false);
  });
});
