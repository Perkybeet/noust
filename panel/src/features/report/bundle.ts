/**
 * The one file of a report: what it holds, what each part is called and how it is written.
 *
 * Everything goes in a single .zip that the person downloads and attaches in GitHub (the form's last box takes
 * it up to 25 MB). The support report comes from the server, already masked; the block of the browser is
 * masked here; the images are exactly the ones the person added. Nothing is sent: the dialog shows every part
 * as it is written here before the file is made.
 */

import type { BrowserBlock } from "./browserBlock";
import type { Shot } from "./images";
import type { ReportFields } from "./githubUrl";
import { zip } from "./zip";

/** The most GitHub takes of one attachment. */
export const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;

export const SUPPORT_FILE = "support-report.md";
export const BROWSER_FILE = "browser.json";
export const DESCRIPTION_FILE = "description.md";
export const IMAGES_FOLDER = "images";

/** One part of the file, as the dialog lists it and as the zip holds it. */
export type ReportFile =
  | { kind: "text"; name: string; text: string; bytes: Uint8Array; holes: number }
  | { kind: "image"; name: string; dataUrl: string; bytes: Uint8Array; holes: 0 };

export interface BundleInput {
  /** The support report's Markdown, when it is included. */
  support: string | null;
  browser: BrowserBlock;
  shots: readonly Shot[];
  /** The whole of what the person wrote, when the address could not carry it all. */
  description: ReportFields | null;
}

const encoder = new TextEncoder();

function textFile(name: string, text: string, holes = 0): ReportFile {
  return { kind: "text", name, text, bytes: encoder.encode(text), holes };
}

/** The text of the person, in full, for the case in which the address had to cut it. */
function describe(fields: ReportFields): string {
  const lines = [`# ${fields.title}`, "", `Noust version: ${fields.version}`, `Where: ${fields.area}`, "", "## What happened", "", fields.whatHappened];
  if (fields.reproduce !== "") lines.push("", "## Steps to reproduce", "", fields.reproduce);
  return `${lines.join("\n")}\n`;
}

/**
 * The parts of the report, in the order they are written.
 *
 * @param input - What goes in it.
 * @returns The files: the support report, the block of the browser, the person's text when it was cut, and one per image.
 */
export function reportFiles(input: BundleInput): ReportFile[] {
  const files: ReportFile[] = [];
  if (input.support !== null) files.push(textFile(SUPPORT_FILE, input.support));
  files.push(textFile(BROWSER_FILE, input.browser.text, input.browser.holes));
  if (input.description !== null) files.push(textFile(DESCRIPTION_FILE, describe(input.description)));
  for (const shot of input.shots) {
    files.push({ kind: "image", name: `${IMAGES_FOLDER}/${shot.name}`, dataUrl: shot.dataUrl, bytes: shot.bytes, holes: 0 });
  }
  return files;
}

/** The bytes the files weigh together, before the zip's few headers. */
export function totalSize(files: readonly ReportFile[]): number {
  return files.reduce((sum, file) => sum + file.bytes.length, 0);
}

/**
 * Writes the parts as one .zip.
 *
 * @param files - The parts.
 * @param now - The time every entry is stamped with.
 * @returns The archive, ready to be downloaded.
 */
export function archive(files: readonly ReportFile[], now: Date = new Date()): Uint8Array<ArrayBuffer> {
  return zip(
    files.map((file) => ({ name: file.name, data: file.bytes })),
    now,
  );
}

function two(value: number): string {
  return String(value).padStart(2, "0");
}

/**
 * The name of the file the person downloads.
 *
 * @param now - When it is made.
 * @returns `noust-report-20261105-1612.zip`, in the person's local time.
 */
export function archiveName(now: Date): string {
  const day = `${String(now.getFullYear())}${two(now.getMonth() + 1)}${two(now.getDate())}`;
  return `noust-report-${day}-${two(now.getHours())}${two(now.getMinutes())}.zip`;
}
