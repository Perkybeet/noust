/**
 * What is drawn while a panel is being arranged, apart from the grid that places it so the design gallery can show it
 * (docs/DESIGN.md, 6.17): the outline of the place a held widget will land, the outline and the name of the size an edge
 * is on its way to, and the grips by which a widget's size is taken. All three are the accent, which is what can be acted
 * on (C-4), and none draws a height.
 */
import type { PointerEvent as ReactPointerEvent, RefObject } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import type { ResizeHandle } from "./dragMath";
import { sizeUnits } from "./model";
import type { SizeKey } from "./model";

/** The name of a size and how much of the panel it is, for what is dragged: "Medium, 2 × 1". */
export function useSizeTag(): (size: SizeKey) => string {
  const t = useT();
  return (size) => {
    const { w, h } = sizeUnits(size);
    return t("widgets.grid.sizeTag", { size: t(`widgets.sizes.${size}`), w, h });
  };
}

/** Where a widget that is held will land: its own place in the panel, at its real size, as an outline that says what size that is. It fills the cell it is put in. */
export function DropPlace({ size }: { size: SizeKey }) {
  const tag = useSizeTag();
  return (
    <div
      aria-hidden="true"
      data-drop-place={size}
      className="absolute inset-0 flex items-center justify-center rounded-card border border-accent bg-accent-soft text-12 font-medium text-accent-fg tabular-nums"
    >
      {tag(size)}
    </div>
  );
}

/**
 * The room a size will take, drawn over whatever is there, and its name. It has no ground: it lies over other widgets,
 * which would be hidden by one. The caller places it and gives it its size (`ref` for what is placed by measuring, `className` for what is placed by a class).
 */
export function SizeOutline({ size, ref, className }: { size: SizeKey; ref?: RefObject<HTMLDivElement | null>; className?: string }) {
  const tag = useSizeTag();
  return (
    <div ref={ref} aria-hidden="true" data-resize-outline={size} className={cx("pointer-events-none absolute flex items-center justify-center rounded-card border-2 border-accent", className)}>
      <span className="rounded-chip bg-accent px-2 py-1 text-12 font-medium text-on-accent tabular-nums">{tag(size)}</span>
    </div>
  );
}

/** One edge or the corner of a widget, drawn while the panel is arranged and with a grip of at least 24px (WCAG 2.5.8). Pointer only: the keyboard has Shift with the arrows and the menu of the size. */
export function Grip({ handle, active, onStart }: { handle: ResizeHandle; active: boolean; onStart: (handle: ResizeHandle, event: ReactPointerEvent<HTMLElement>) => void }) {
  if (handle === "corner") {
    return (
      <div
        data-resize="corner"
        aria-hidden="true"
        onPointerDown={(event) => onStart(handle, event)}
        className={cx("group absolute right-0 bottom-0 flex size-7 cursor-nwse-resize touch-none items-end justify-end p-1.5", active ? "text-accent-fg" : "text-fg-muted hover:text-fg")}
      >
        <svg viewBox="0 0 10 10" className="size-2.5">
          <path d="M9 1 1 9M9 5 5 9M9 8.5 8.5 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
        </svg>
      </div>
    );
  }
  const right = handle === "right";
  return (
    <div
      data-resize-edge={handle}
      aria-hidden="true"
      onPointerDown={(event) => onStart(handle, event)}
      // The right edge starts under the head, where the controls are, and both stop short of the corner, which is the corner's.
      className={cx(
        "group absolute flex touch-none",
        right ? "top-10 right-0 bottom-7 w-6 cursor-ew-resize items-center justify-end pr-1" : "right-7 bottom-0 left-4 h-6 cursor-ns-resize items-end justify-center pb-1",
      )}
    >
      {/* A knob with an edge of its own: a bare dark bar down a card is read as its scrollbar. */}
      <span
        className={cx(
          "rounded-pill border",
          right ? "h-6 w-1.5" : "h-1.5 w-6",
          active ? "border-accent bg-accent" : "border-border-strong bg-surface-raised group-hover:border-accent group-hover:bg-accent-soft",
        )}
      />
    </div>
  );
}
