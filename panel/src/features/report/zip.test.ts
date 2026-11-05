import { describe, expect, it } from "vitest";

import { crc32, zip } from "./zip";

const text = (value: string): Uint8Array => new TextEncoder().encode(value);

/** The checksum computed a bit at a time, from its definition: the polynomial 0xEDB88320 reflected, no table. */
function slowCrc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;
  }
  return (crc ^ 0xffffffff) >>> 0;
}

interface Found {
  name: string;
  data: Uint8Array;
  crc: number;
  method: number;
  flags: number;
}

/** Reads an archive the way an unzipper does: from the directory at the end, never from the headers before the data. */
function read(bytes: Uint8Array): Found[] {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let end = bytes.length - 22;
  while (end >= 0 && view.getUint32(end, true) !== 0x06054b50) end -= 1;
  expect(end).toBeGreaterThanOrEqual(0);
  const count = view.getUint16(end + 10, true);
  const size = view.getUint32(end + 12, true);
  let at = view.getUint32(end + 16, true);
  expect(at + size).toBe(end);
  const found: Found[] = [];
  for (let index = 0; index < count; index += 1) {
    expect(view.getUint32(at, true)).toBe(0x02014b50);
    const flags = view.getUint16(at + 8, true);
    const method = view.getUint16(at + 10, true);
    const crc = view.getUint32(at + 16, true);
    const compressed = view.getUint32(at + 20, true);
    const original = view.getUint32(at + 24, true);
    const nameLength = view.getUint16(at + 28, true);
    const extraLength = view.getUint16(at + 30, true);
    const commentLength = view.getUint16(at + 32, true);
    const local = view.getUint32(at + 42, true);
    const name = new TextDecoder().decode(bytes.subarray(at + 46, at + 46 + nameLength));
    expect(compressed).toBe(original);
    expect(view.getUint32(local, true)).toBe(0x04034b50);
    const localName = view.getUint16(local + 26, true);
    const localExtra = view.getUint16(local + 28, true);
    const start = local + 30 + localName + localExtra;
    found.push({ name, data: bytes.slice(start, start + original), crc, method, flags });
    at += 46 + nameLength + extraLength + commentLength;
  }
  return found;
}

describe("the CRC-32", () => {
  it("is the standard one: the check value of the ASCII digits", () => {
    expect(crc32(text("123456789"))).toBe(0xcbf43926);
    expect(crc32(new Uint8Array())).toBe(0);
  });

  it("agrees with the definition, computed a bit at a time, on bytes of every value", () => {
    const bytes = Uint8Array.from({ length: 5000 }, (_value, index) => (index * 31 + 7) % 256);

    expect(crc32(bytes)).toBe(slowCrc32(bytes));
  });
});

describe("a zip written in the browser", () => {
  it("holds each file under its name with its bytes and its checksum", () => {
    const png = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0, 255, 128, 1]);
    const archive = zip([
      { name: "support-report.md", data: text("# Noust support report\n") },
      { name: "browser.json", data: text('{"page":"/databases"}') },
      { name: "images/capture-1.png", data: png },
    ]);

    const files = read(archive);

    expect(files.map((file) => file.name)).toEqual(["support-report.md", "browser.json", "images/capture-1.png"]);
    expect(new TextDecoder().decode(files[0]?.data)).toBe("# Noust support report\n");
    expect(Array.from(files[2]?.data ?? [])).toEqual(Array.from(png));
    for (const file of files) {
      expect(file.method).toBe(0);
      expect(file.crc).toBe(slowCrc32(file.data));
    }
  });

  it("writes a name with an accent as UTF-8 and says so", () => {
    const [file] = read(zip([{ name: "capturas/pestaña.png", data: text("x") }]));

    expect(file?.name).toBe("capturas/pestaña.png");
    expect((file?.flags ?? 0) & 0x0800).toBe(0x0800);
  });

  it("is a valid archive with no files at all", () => {
    const archive = zip([]);

    expect(archive).toHaveLength(22);
    expect(read(archive)).toEqual([]);
  });

  it("holds an empty file", () => {
    const [file] = read(zip([{ name: "empty.txt", data: new Uint8Array() }]));

    expect(file?.data).toHaveLength(0);
    expect(file?.crc).toBe(0);
  });

  it("stamps the entries with the time it is given, in the format of MS-DOS", () => {
    const archive = zip([{ name: "a", data: text("a") }], new Date(2026, 10, 5, 16, 12, 30));
    const view = new DataView(archive.buffer);

    // The local header: time at 10, date at 12.
    expect(view.getUint16(10, true)).toBe((16 << 11) | (12 << 5) | 15);
    expect(view.getUint16(12, true)).toBe(((2026 - 1980) << 9) | (11 << 5) | 5);
  });

  it("refuses more files than a plain zip can hold", () => {
    const many = Array.from({ length: 65536 }, (_value, index) => ({ name: String(index), data: new Uint8Array() }));

    expect(() => zip(many)).toThrow(/65535/);
  });
});
