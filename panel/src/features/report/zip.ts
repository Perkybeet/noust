/**
 * A .zip written in the browser, with no library.
 *
 * The report of a problem is one file, and a file with a folder of images in it has to be a
 * standard archive that every system opens. The method "store" (no compression) is enough and keeps
 * this to a page: the images are already compressed, and the text is small. What the format needs
 * is each entry's CRC-32, a header before its bytes, and a directory at the end; nothing else.
 * Names are written as UTF-8 (flag 11), so a name with an accent is read as it was written.
 */

const LOCAL_HEADER = 0x04034b50;
const CENTRAL_HEADER = 0x02014b50;
const END_OF_DIRECTORY = 0x06054b50;
/** "Version needed to extract": 2.0, the one that has folders and UTF-8 names. */
const VERSION = 20;
/** General purpose flag 11: the names are UTF-8. */
const UTF8_NAMES = 0x0800;
/** What the format cannot say without the ZIP64 extension, which a report never needs. */
const MAX_SIZE = 0xffffffff;
const MAX_ENTRIES = 0xffff;

export interface ZipEntry {
  /** The path inside the archive, with `/` between folders and no leading `/`. */
  name: string;
  data: Uint8Array;
}

const TABLE = ((): Uint32Array => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    table[index] = value >>> 0;
  }
  return table;
})();

/**
 * The CRC-32 of some bytes (the one zip, gzip and PNG use).
 *
 * @param data - The bytes.
 * @returns The checksum, as an unsigned 32-bit number.
 */
export function crc32(data: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of data) crc = (TABLE[(crc ^ byte) & 0xff] ?? 0) ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

/** The date and time as the two 16-bit fields of an entry (MS-DOS format, local time, two-second steps). */
function dosDateTime(moment: Date): { date: number; time: number } {
  const year = Math.max(1980, moment.getFullYear());
  return {
    date: ((year - 1980) << 9) | ((moment.getMonth() + 1) << 5) | moment.getDate(),
    time: (moment.getHours() << 11) | (moment.getMinutes() << 5) | (moment.getSeconds() >> 1),
  };
}

/**
 * Writes entries as a .zip, stored.
 *
 * @param entries - The files, in the order they are written.
 * @param modified - The time every entry is stamped with.
 * @returns The bytes of the archive.
 * @throws Error when there are more entries or bytes than a plain zip can hold.
 */
export function zip(entries: readonly ZipEntry[], modified: Date = new Date()): Uint8Array<ArrayBuffer> {
  if (entries.length > MAX_ENTRIES) throw new Error("A zip holds 65535 files at most.");
  const encoder = new TextEncoder();
  const stamp = dosDateTime(modified);
  const named = entries.map((entry) => ({ ...entry, bytes: encoder.encode(entry.name), crc: crc32(entry.data) }));
  const total = named.reduce((sum, entry) => sum + 30 + entry.bytes.length + entry.data.length + 46 + entry.bytes.length, 22);
  if (total > MAX_SIZE) throw new Error("A zip of this size needs the ZIP64 extension, which a report never does.");

  const out = new Uint8Array(total);
  const view = new DataView(out.buffer);
  let at = 0;
  const offsets: number[] = [];
  for (const entry of named) {
    offsets.push(at);
    view.setUint32(at, LOCAL_HEADER, true);
    view.setUint16(at + 4, VERSION, true);
    view.setUint16(at + 6, UTF8_NAMES, true);
    view.setUint16(at + 8, 0, true); // method 0: stored
    view.setUint16(at + 10, stamp.time, true);
    view.setUint16(at + 12, stamp.date, true);
    view.setUint32(at + 14, entry.crc, true);
    view.setUint32(at + 18, entry.data.length, true);
    view.setUint32(at + 22, entry.data.length, true);
    view.setUint16(at + 26, entry.bytes.length, true);
    view.setUint16(at + 28, 0, true);
    out.set(entry.bytes, at + 30);
    out.set(entry.data, at + 30 + entry.bytes.length);
    at += 30 + entry.bytes.length + entry.data.length;
  }
  const directory = at;
  named.forEach((entry, index) => {
    view.setUint32(at, CENTRAL_HEADER, true);
    view.setUint16(at + 4, VERSION, true); // made by
    view.setUint16(at + 6, VERSION, true); // needed to extract
    view.setUint16(at + 8, UTF8_NAMES, true);
    view.setUint16(at + 10, 0, true);
    view.setUint16(at + 12, stamp.time, true);
    view.setUint16(at + 14, stamp.date, true);
    view.setUint32(at + 16, entry.crc, true);
    view.setUint32(at + 20, entry.data.length, true);
    view.setUint32(at + 24, entry.data.length, true);
    view.setUint16(at + 28, entry.bytes.length, true);
    view.setUint16(at + 30, 0, true); // extra
    view.setUint16(at + 32, 0, true); // comment
    view.setUint16(at + 34, 0, true); // disk
    view.setUint16(at + 36, 0, true); // internal attributes
    view.setUint32(at + 38, 0, true); // external attributes
    view.setUint32(at + 42, offsets[index] ?? 0, true);
    out.set(entry.bytes, at + 46);
    at += 46 + entry.bytes.length;
  });
  view.setUint32(at, END_OF_DIRECTORY, true);
  view.setUint16(at + 4, 0, true);
  view.setUint16(at + 6, 0, true);
  view.setUint16(at + 8, named.length, true);
  view.setUint16(at + 10, named.length, true);
  view.setUint32(at + 12, at - directory, true);
  view.setUint32(at + 16, directory, true);
  view.setUint16(at + 20, 0, true);
  return out;
}
