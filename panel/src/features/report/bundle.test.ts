import { describe, expect, it } from "vitest";

import { BROWSER_FILE, DESCRIPTION_FILE, SUPPORT_FILE, archive, archiveName, reportFiles, totalSize } from "./bundle";
import type { BundleInput } from "./bundle";
import type { Shot } from "./images";

const SHOT: Shot = { id: "1", name: "capture-1.png", type: "image/png", size: 4, dataUrl: "data:image/png;base64,iVBORw==", bytes: Uint8Array.from([0x89, 0x50, 0x4e, 0x47]) };

const INPUT: BundleInput = {
  support: "# Noust support report\n\n## Noust\n\n```\n4.2.0\n```\n",
  browser: { text: '{\n  "page": "/databases"\n}\n', holes: 1 },
  shots: [SHOT],
  description: null,
};

describe("the parts of a report", () => {
  it("are the support report, the browser and the images, in that order, each under its own name", () => {
    const files = reportFiles(INPUT);

    expect(files.map((file) => file.name)).toEqual([SUPPORT_FILE, BROWSER_FILE, "images/capture-1.png"]);
    expect(files.map((file) => file.kind)).toEqual(["text", "text", "image"]);
  });

  it("leave out the support report when it is not included", () => {
    expect(reportFiles({ ...INPUT, support: null }).map((file) => file.name)).toEqual([BROWSER_FILE, "images/capture-1.png"]);
  });

  it("keep the hidden count of the browser's block, and give an image its bytes and its address", () => {
    const [, browser, image] = reportFiles(INPUT);

    expect(browser?.holes).toBe(1);
    expect(image?.kind === "image" ? image.dataUrl : null).toBe(SHOT.dataUrl);
    expect(Array.from(image?.bytes ?? [])).toEqual(Array.from(SHOT.bytes));
  });

  it("hold the whole of what the person wrote when the address had to cut it", () => {
    const files = reportFiles({
      ...INPUT,
      description: { title: "It broke", version: "4.2.0", area: "Databases", whatHappened: "A very long account.", reproduce: "1. Open it." },
    });
    const description = files.find((file) => file.name === DESCRIPTION_FILE);

    expect(description?.kind === "text" ? description.text : "").toBe(
      "# It broke\n\nNoust version: 4.2.0\nWhere: Databases\n\n## What happened\n\nA very long account.\n\n## Steps to reproduce\n\n1. Open it.\n",
    );
  });

  it("weigh what their bytes weigh", () => {
    const files = reportFiles(INPUT);

    expect(totalSize(files)).toBe(files.reduce((sum, file) => sum + file.bytes.length, 0));
    expect(totalSize([])).toBe(0);
  });
});

describe("the file that is downloaded", () => {
  it("is named by the day and the minute, in local time", () => {
    expect(archiveName(new Date(2026, 10, 5, 16, 2))).toBe("noust-report-20261105-1602.zip");
    expect(archiveName(new Date(2027, 0, 9, 4, 30))).toBe("noust-report-20270109-0430.zip");
  });

  it("is a zip that holds every part", () => {
    const bytes = archive(reportFiles(INPUT), new Date(2026, 10, 5, 16, 12));
    const view = new DataView(bytes.buffer);

    expect(view.getUint32(0, true)).toBe(0x04034b50);
    expect(view.getUint16(bytes.length - 12, true)).toBe(3);
    expect(new TextDecoder().decode(bytes)).toContain("# Noust support report");
  });
});
