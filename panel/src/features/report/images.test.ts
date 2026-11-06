import { afterEach, describe, expect, it, vi } from "vitest";

import { CaptureRefused, MAX_IMAGE_BYTES, captureTab, decodeDataUrl, extensionOf, readImage, safeFileName, shotFromDataUrl, uniqueName, wasTaken } from "./images";

// A 1x1 PNG.
const PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg==";

function png(name = "shot.png", type = "image/png"): File {
  return new File([Uint8Array.from(atob(PNG_BASE64), (character) => character.charCodeAt(0))], name, { type });
}

describe("names in the zip", () => {
  it("keeps the letters of any language and replaces what a path cannot hold", () => {
    expect(safeFileName("Captura de pantalla 2026-11-05.png")).toBe("Captura-de-pantalla-2026-11-05.png");
    expect(safeFileName("pestaña (1).png")).toBe("pestaña-1.png");
    expect(safeFileName("画面.png")).toBe("画面.png");
  });

  it("drops folders and a leading dot, so a name cannot walk out of its folder", () => {
    expect(safeFileName("../../etc/passwd")).toBe("passwd");
    expect(safeFileName("C:\\Users\\yo\\a.png")).toBe("a.png");
    expect(safeFileName(".hidden.png")).toBe("hidden.png");
    expect(safeFileName("///")).toBe("");
  });

  it("is short enough", () => {
    expect(safeFileName(`${"a".repeat(300)}.png`).length).toBeLessThanOrEqual(80);
  });

  it("says each name once, before its extension", () => {
    expect(uniqueName("a.png", [])).toBe("a.png");
    expect(uniqueName("a.png", ["a.png"])).toBe("a-2.png");
    expect(uniqueName("a.png", ["a.png", "a-2.png"])).toBe("a-3.png");
    expect(uniqueName("noextension", ["noextension"])).toBe("noextension-2");
  });

  it("writes the extension of a type", () => {
    expect(extensionOf("image/jpeg")).toBe("jpg");
    expect(extensionOf("image/webp")).toBe("webp");
    expect(extensionOf("image/other")).toBe("png");
  });
});

describe("a data address", () => {
  it("is read back to the bytes it was made of", () => {
    const decoded = decodeDataUrl(`data:image/png;base64,${PNG_BASE64}`);

    expect(decoded?.type).toBe("image/png");
    expect(Array.from(decoded?.bytes.slice(0, 4) ?? [])).toEqual([0x89, 0x50, 0x4e, 0x47]);
  });

  it("is refused when it is not base64 or not a data address", () => {
    expect(decodeDataUrl("data:text/plain,hello")).toBeNull();
    expect(decodeDataUrl("https://example.com/a.png")).toBeNull();
    expect(decodeDataUrl("data:image/png;base64,%%%")).toBeNull();
  });
});

describe("taking an image", () => {
  it("reads a chosen file as a data address and keeps its bytes", async () => {
    const result = await readImage(png(), "shot.png", "1");

    expect(wasTaken(result)).toBe(true);
    if (!wasTaken(result)) return;
    expect(result.dataUrl).toBe(`data:image/png;base64,${PNG_BASE64}`);
    expect(result.type).toBe("image/png");
    expect(result.size).toBe(result.bytes.length);
    expect(result.name).toBe("shot.png");
  });

  it("refuses what is not an image of a format a report takes, SVG included", async () => {
    const svg = new File(["<svg xmlns='http://www.w3.org/2000/svg'/>"], "a.svg", { type: "image/svg+xml" });
    const text = new File(["hello"], "a.txt", { type: "text/plain" });

    expect(await readImage(svg, "a.svg", "1")).toEqual({ name: "a.svg", reason: "type" });
    expect(await readImage(text, "a.txt", "2")).toEqual({ name: "a.txt", reason: "type" });
  });

  it("refuses an image that is too big without reading it", async () => {
    const big = new File([new Uint8Array(MAX_IMAGE_BYTES + 1)], "big.png", { type: "image/png" });

    expect(await readImage(big, "big.png", "1")).toEqual({ name: "big.png", reason: "size" });
  });

  it("makes an image of a data address a capture returns", () => {
    const shot = shotFromDataUrl(`data:image/png;base64,${PNG_BASE64}`, "tab-1.png", "7");

    expect(shot?.id).toBe("7");
    expect(shot?.name).toBe("tab-1.png");
    expect(shot?.bytes.length).toBeGreaterThan(0);
    expect(shotFromDataUrl("nope", "x.png", "8")).toBeNull();
  });
});

describe("capturing this tab", () => {
  const original = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");

  afterEach(() => {
    if (original !== undefined) Object.defineProperty(navigator, "mediaDevices", original);
    else Reflect.deleteProperty(navigator, "mediaDevices");
  });

  it("is refused where the browser cannot capture", async () => {
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: undefined });

    await expect(captureTab(() => Promise.resolve(), () => undefined)).rejects.toBeInstanceOf(CaptureRefused);
  });

  it("is refused when the person says no, and the dialog was never hidden", async () => {
    const hide = vi.fn(() => Promise.resolve());
    const show = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getDisplayMedia: () => Promise.reject(new DOMException("denied", "NotAllowedError")) },
    });

    await expect(captureTab(hide, show)).rejects.toBeInstanceOf(CaptureRefused);

    expect(hide).not.toHaveBeenCalled();
    expect(show).not.toHaveBeenCalled();
  });

  it("hides the dialog after the person chose, takes the frame, stops the share and shows the dialog again", async () => {
    const order: string[] = [];
    const stop = vi.fn(() => {
      order.push("stop");
    });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getDisplayMedia: () => {
          order.push("chosen");
          return Promise.resolve({ getTracks: () => [{ stop }] });
        },
      },
    });
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 0;
    });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage: () => undefined } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(`data:image/png;base64,${PNG_BASE64}`);

    const result = await captureTab(
      () => {
        order.push("hidden");
        return Promise.resolve();
      },
      () => {
        order.push("shown");
      },
    );

    expect(result).toBe(`data:image/png;base64,${PNG_BASE64}`);
    expect(order).toEqual(["chosen", "hidden", "stop", "shown"]);
  });
});
