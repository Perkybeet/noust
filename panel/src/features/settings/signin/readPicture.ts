import type { SignInLimits } from "../../../api/queries/signInImage";

/** The types the server accepts, for a server that does not list them. */
const FALLBACK_TYPES: readonly string[] = ["image/png", "image/jpeg", "image/webp"];

/** A picture the operator chose and the console read in the browser, ready to preview and to send. */
export interface ChosenPicture {
  /** The file's name, for the line that says what was chosen. */
  name: string;
  size: number;
  /** For the preview: the console's policy allows `data:` images and not `blob:` ones. */
  dataUrl: string;
  /** What the API takes: the file as base64, without the `data:` prefix. */
  base64: string;
}

/** Why a file is not offered for upload before the server is asked: the two things the browser knows. */
export type PictureProblem = { kind: "too-large"; size: number } | { kind: "not-allowed" } | { kind: "unreadable" };

/**
 * Whether a file is worth sending, judged only by what the browser knows: its size and the type it
 * reports. The server is the one that judges a picture (by its bytes, its dimensions and its
 * metadata); this only spares a megabyte of upload that cannot be accepted, and says an SVG is not one
 * in the person's language before they wait for the answer. A file with no type is sent on and judged there.
 *
 * @param file The file the person chose or dropped.
 * @param limits What the server says a picture may be.
 * @returns The problem, or null when it may be sent.
 */
export function judgeFile(file: Pick<File, "size" | "type">, limits: Pick<SignInLimits, "bytes" | "types">): PictureProblem | null {
  if (file.type !== "" && !(limits.types ?? FALLBACK_TYPES).includes(file.type)) return { kind: "not-allowed" };
  if (file.size > limits.bytes) return { kind: "too-large", size: file.size };
  return null;
}

/**
 * Reads a file as a `data:` URL, once, which is both the preview and (past its prefix) the body of the upload.
 *
 * @param file The file.
 * @returns The picture, or null when the browser cannot read it.
 */
export function readPicture(file: File): Promise<ChosenPicture | null> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onerror = () => {
      resolve(null);
    };
    reader.onload = () => {
      const dataUrl = typeof reader.result === "string" ? reader.result : "";
      const comma = dataUrl.indexOf(",");
      if (comma < 0) {
        resolve(null);
        return;
      }
      resolve({ name: file.name, size: file.size, dataUrl, base64: dataUrl.slice(comma + 1) });
    };
    reader.readAsDataURL(file);
  });
}
