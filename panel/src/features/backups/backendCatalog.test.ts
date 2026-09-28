import { describe, expect, it } from "vitest";

import { OAUTH_BACKENDS, backendDescription, backendLabel } from "./backendCatalog";

describe("backendCatalog", () => {
  it("gives every known backend a human label", () => {
    expect(backendLabel("s3")).toBe("S3-compatible storage");
    expect(backendLabel("sftp")).toBe("SFTP server");
  });

  it("falls back to the raw name for a backend the console does not know yet", () => {
    expect(backendLabel("some-new-backend")).toBe("some-new-backend");
    expect(backendDescription("some-new-backend")).toBeUndefined();
  });

  it("marks exactly the backends that sign in with a token pasted from rclone authorize", () => {
    expect(OAUTH_BACKENDS.has("drive")).toBe(true);
    expect(OAUTH_BACKENDS.has("onedrive")).toBe(true);
    expect(OAUTH_BACKENDS.has("dropbox")).toBe(true);
    expect(OAUTH_BACKENDS.has("pcloud")).toBe(true);
    expect(OAUTH_BACKENDS.has("s3")).toBe(false);
    expect(OAUTH_BACKENDS.has("sftp")).toBe(false);
  });
});
