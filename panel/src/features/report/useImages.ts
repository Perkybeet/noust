import { useCallback, useEffect, useRef, useState } from "react";

import { useT } from "../../i18n";
import { formatBytes } from "../../lib/format";
import {
  CaptureRefused,
  MAX_IMAGE_BYTES,
  captureTab,
  extensionOf,
  readImage,
  safeFileName,
  shotFromDataUrl,
  uniqueName,
  wasTaken,
} from "./images";
import type { Refusal, Shot } from "./images";

export interface Images {
  shots: readonly Shot[];
  /** What could not be taken, in words, to say once under the images. */
  notes: readonly string[];
  /** Chosen with the file picker (`source: "file"`) or pasted (`"paste"`, whose names are the clipboard's, so they are replaced). */
  add: (files: readonly File[], source: "file" | "paste") => Promise<void>;
  remove: (id: string) => void;
  /** A picture of this tab; `hide` and `show` take the dialog off the screen for the frame and put it back. */
  capture: (hide: () => Promise<void>, show: () => void) => Promise<void>;
}

/**
 * The images of a report: what the person has added, what could not be, and the ways to add one.
 *
 * @param listening - Whether a paste of an image is taken (only while the step that shows them is on screen).
 * @returns The list and its operations.
 */
export function useImages(listening: boolean): Images {
  const t = useT();
  const [shots, setShots] = useState<readonly Shot[]>([]);
  const [notes, setNotes] = useState<readonly string[]>([]);
  // What the handlers below see of the list at the moment they run, not at the render that made them.
  const latest = useRef<readonly Shot[]>(shots);
  useEffect(() => {
    latest.current = shots;
  }, [shots]);
  const counter = useRef(0);

  const sayRefusal = useCallback(
    (refusal: Refusal): string => t(`report.what.refused.${refusal.reason}`, { name: refusal.name, max: formatBytes(MAX_IMAGE_BYTES, t.locale) }),
    [t],
  );

  const add = useCallback(
    async (files: readonly File[], source: "file" | "paste"): Promise<void> => {
      const taken: Shot[] = [];
      const refused: string[] = [];
      for (const file of files) {
        counter.current += 1;
        const own = source === "file" ? safeFileName(file.name) : "";
        const wanted = own !== "" ? own : `pasted-${String(counter.current)}.${extensionOf(file.type)}`;
        const name = uniqueName(wanted, [...latest.current.map((shot) => shot.name), ...taken.map((shot) => shot.name)]);
        const result = await readImage(file, name, String(counter.current));
        if (wasTaken(result)) taken.push(result);
        else refused.push(sayRefusal(result));
      }
      setShots((all) => [...all, ...taken]);
      setNotes(refused);
    },
    [sayRefusal],
  );

  const remove = useCallback((id: string) => {
    setShots((all) => all.filter((shot) => shot.id !== id));
    setNotes([]);
  }, []);

  const capture = useCallback(
    async (hide: () => Promise<void>, show: () => void): Promise<void> => {
      setNotes([]);
      try {
        const dataUrl = await captureTab(hide, show);
        counter.current += 1;
        const name = uniqueName(`tab-${String(counter.current)}.png`, latest.current.map((shot) => shot.name));
        const shot = shotFromDataUrl(dataUrl, name, String(counter.current));
        if (shot === null) setNotes([sayRefusal({ name, reason: "unreadable" })]);
        else setShots((all) => [...all, shot]);
      } catch (error: unknown) {
        if (!(error instanceof CaptureRefused)) throw error;
        setNotes([t("report.what.captureRefused")]);
      }
    },
    [sayRefusal, t],
  );

  // A pasted image goes to the report; a pasted text goes where the person pasted it.
  useEffect(() => {
    if (!listening) return;
    const onPaste = (event: ClipboardEvent): void => {
      const images = Array.from(event.clipboardData?.files ?? []).filter((file) => file.type.startsWith("image/"));
      if (images.length === 0) return;
      event.preventDefault();
      void add(images, "paste");
    };
    document.addEventListener("paste", onPaste);
    return () => {
      document.removeEventListener("paste", onPaste);
    };
  }, [listening, add]);

  return { shots, notes, add, remove, capture };
}
