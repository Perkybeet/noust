import { describe, expect, it } from "vitest";

import { judgeFile, readPicture } from "./readPicture";

const LIMITS = { bytes: 1024 * 1024, types: ["image/png", "image/jpeg", "image/webp"] };

describe("what the browser judges of a file before it is sent", () => {
  it("lets through a picture of one of the three types within the size", () => {
    for (const type of LIMITS.types) expect(judgeFile({ size: 5_000, type }, LIMITS)).toBeNull();
    expect(judgeFile({ size: LIMITS.bytes, type: "image/png" }, LIMITS)).toBeNull();
  });

  it("says a file over the limit is too large, with its size", () => {
    expect(judgeFile({ size: LIMITS.bytes + 1, type: "image/png" }, LIMITS)).toEqual({ kind: "too-large", size: LIMITS.bytes + 1 });
  });

  it("refuses the types the server would refuse, an SVG first among them", () => {
    for (const type of ["image/svg+xml", "image/gif", "text/html", "application/zip"]) {
      expect(judgeFile({ size: 10, type }, LIMITS)).toEqual({ kind: "not-allowed" });
    }
  });

  it("sends on a file that reports no type: the server judges what it is by its bytes", () => {
    expect(judgeFile({ size: 10, type: "" }, LIMITS)).toBeNull();
  });

  it("knows the three types when the server does not list them", () => {
    expect(judgeFile({ size: 10, type: "image/webp" }, { bytes: LIMITS.bytes })).toBeNull();
    expect(judgeFile({ size: 10, type: "image/svg+xml" }, { bytes: LIMITS.bytes })).toEqual({ kind: "not-allowed" });
  });
});

describe("reading a file once", () => {
  it("gives the data address for the preview and the base64 that is its body for the upload", async () => {
    const file = new File([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])], "logo.png", { type: "image/png" });

    const picture = await readPicture(file);

    expect(picture).toEqual({
      name: "logo.png",
      size: 8,
      dataUrl: "data:image/png;base64,iVBORw0KGgo=",
      base64: "iVBORw0KGgo=",
    });
  });

  it("is a data address and never a blob one, which the console's policy does not allow images from", async () => {
    const picture = await readPicture(new File(["x"], "a.png", { type: "image/png" }));

    expect(picture?.dataUrl.startsWith("data:")).toBe(true);
  });
});
