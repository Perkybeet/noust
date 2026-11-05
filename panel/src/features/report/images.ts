/**
 * The images of a report: chosen, pasted or taken of this tab.
 *
 * Each is read as a `data:` address and kept as its bytes. The console's policy lets a page draw
 * `data:` images and not `blob:` ones (`img-src 'self' data:`), so the preview in the dialog is the data address
 * itself and the zip is written from the bytes the address decodes to.
 */

/** What an image may be: the formats a maintainer's browser shows in an issue. Not SVG, which is code. */
export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"] as const;

/** The most one image may weigh: GitHub takes a file of 25 MB at most, and the report holds several things. */
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;

export interface Shot {
  id: string;
  /** The file's name inside the zip, without its folder. */
  name: string;
  type: string;
  size: number;
  /** `data:image/png;base64,...`, what the preview draws. */
  dataUrl: string;
  bytes: Uint8Array;
}

/** Why a file was not taken. */
export interface Refusal {
  name: string;
  reason: "type" | "size" | "unreadable";
}

const EXTENSIONS: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif" };

/** The extension a type is written with. */
export function extensionOf(type: string): string {
  return EXTENSIONS[type] ?? "png";
}

/**
 * Turns a name a person's system gave a file into one that is safe as a path in a zip.
 *
 * @param name - The file's name, as it came.
 * @returns The name with no folders, no spaces or characters a path cannot hold, and no dot at its start.
 */
export function safeFileName(name: string): string {
  const base = name.split(/[\\/]/).at(-1) ?? "";
  const cleaned = base
    .replace(/[^\p{L}\p{N}._-]+/gu, "-")
    .replace(/^[.-]+/, "")
    .replace(/-+(\.|$)/g, "$1");
  return cleaned.slice(0, 80);
}

/**
 * Gives a name that no other image in the list has.
 *
 * @param name - The wanted name.
 * @param taken - The names already in the list.
 * @returns The name, or the name with `-2`, `-3`... before its extension.
 */
export function uniqueName(name: string, taken: readonly string[]): string {
  if (!taken.includes(name)) return name;
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const extension = dot > 0 ? name.slice(dot) : "";
  for (let n = 2; ; n += 1) {
    const candidate = `${stem}-${String(n)}${extension}`;
    if (!taken.includes(candidate)) return candidate;
  }
}

/**
 * Reads a `data:` address.
 *
 * @param dataUrl - `data:<type>;base64,<data>`.
 * @returns Its type and bytes; null when it is not a base64 data address.
 */
export function decodeDataUrl(dataUrl: string): { type: string; bytes: Uint8Array } | null {
  const match = /^data:([^;,]+);base64,([A-Za-z0-9+/=\s]*)$/.exec(dataUrl);
  if (match === null) return null;
  try {
    const binary = atob(match[2] ?? "");
    return { type: match[1] ?? "", bytes: Uint8Array.from(binary, (character) => character.charCodeAt(0)) };
  } catch {
    return null;
  }
}

function readAsDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      resolve(typeof reader.result === "string" ? reader.result : "");
    };
    reader.onerror = () => {
      reject(reader.error ?? new Error("The file could not be read."));
    };
    reader.readAsDataURL(blob);
  });
}

/**
 * Takes an image a person chose or pasted.
 *
 * @param blob - The file.
 * @param name - The name to keep it under (already safe and unique).
 * @param id - What identifies it in the list.
 * @returns The image, or why it was refused: not an image of a format a report takes, too big, or unreadable.
 */
export async function readImage(blob: Blob, name: string, id: string): Promise<Shot | Refusal> {
  if (!(IMAGE_TYPES as readonly string[]).includes(blob.type)) return { name, reason: "type" };
  if (blob.size > MAX_IMAGE_BYTES) return { name, reason: "size" };
  try {
    const dataUrl = await readAsDataUrl(blob);
    const decoded = decodeDataUrl(dataUrl);
    if (decoded === null) return { name, reason: "unreadable" };
    return { id, name, type: blob.type, size: decoded.bytes.length, dataUrl, bytes: decoded.bytes };
  } catch {
    return { name, reason: "unreadable" };
  }
}

/** Whether `readImage` took the file. */
export function wasTaken(result: Shot | Refusal): result is Shot {
  return "dataUrl" in result;
}

/** The browser did not let the tab be captured: the person said no, or this browser cannot. */
export class CaptureRefused extends Error {
  constructor(options?: { cause?: unknown }) {
    super("The browser did not allow capturing this tab.", options);
    this.name = "CaptureRefused";
  }
}

/** Waits for the page to paint twice, so what was hidden is gone from the frame a capture takes. */
function nextPaint(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        resolve();
      });
    });
  });
}

/**
 * Takes a picture of this tab, with the browser's permission.
 *
 * The browser asks which tab to share; once it has been chosen, `hide` takes the dialog off the screen (the
 * picture is of the page the problem is on, not of the dialog over it), a frame is drawn, and the share ends.
 *
 * @param hide - Takes the report's dialog off the screen and says when it is gone.
 * @param show - Puts it back, whatever happened.
 * @returns A PNG as a `data:` address.
 * @throws CaptureRefused when the browser cannot capture or the person did not allow it.
 */
export async function captureTab(hide: () => Promise<void>, show: () => void): Promise<string> {
  const devices = navigator.mediaDevices as MediaDevices | undefined;
  if (typeof devices?.getDisplayMedia !== "function") throw new CaptureRefused();
  let stream: MediaStream;
  try {
    stream = await devices.getDisplayMedia({ video: true, preferCurrentTab: true } as DisplayMediaStreamOptions);
  } catch (cause: unknown) {
    throw new CaptureRefused({ cause });
  }
  try {
    await hide();
    await nextPaint();
    const video = document.createElement("video");
    video.muted = true;
    video.srcObject = stream;
    await video.play();
    await nextPaint();
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    return canvas.toDataURL("image/png");
  } finally {
    for (const track of stream.getTracks()) track.stop();
    show();
  }
}

/**
 * Makes an image of a `data:` address the capture returned.
 *
 * @param dataUrl - The PNG.
 * @param name - The name to keep it under.
 * @param id - What identifies it in the list.
 * @returns The image; null when the address cannot be read.
 */
export function shotFromDataUrl(dataUrl: string, name: string, id: string): Shot | null {
  const decoded = decodeDataUrl(dataUrl);
  if (decoded === null) return null;
  return { id, name, type: decoded.type, size: decoded.bytes.length, dataUrl, bytes: decoded.bytes };
}
