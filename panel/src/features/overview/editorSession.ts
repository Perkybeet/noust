/**
 * What the gallery needs of the panel that is being customised, which are two different parts of the frame: the
 * panel is the page and the gallery is the column beside the main bar. The page, while it is customised, says what
 * it holds and how to add to it and to take hold of a widget for it; the column reads that and draws the gallery. Nothing
 * of it outlives the page: it is gone when the page is.
 */

import { useSyncExternalStore } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import type { WidgetDef } from "../../components/widgets/catalog";
import type { Placed } from "../../components/widgets/model";
import type { PanelScope } from "../../components/widgets/types";

export interface EditorSession {
  scope: PanelScope;
  /** What the panel holds now. */
  layout: readonly Placed[];
  /** The panel holds as many widgets as it may. */
  full: boolean;
  /** Puts a widget at the end of the panel. */
  add: (widget: WidgetDef) => void;
  /** Takes hold of a widget for the panel, with a pointer. */
  startDrag: (widget: WidgetDef, event: ReactPointerEvent<HTMLElement>) => void;
}

let session: EditorSession | null = null;
const listeners = new Set<() => void>();

/** The page says what it holds, or, with null, that it is no longer being customised. */
export function setEditorSession(next: EditorSession | null): void {
  session = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The panel being customised, or null when none is. */
export function useEditorSession(): EditorSession | null {
  return useSyncExternalStore(
    subscribe,
    () => session,
    () => null,
  );
}
