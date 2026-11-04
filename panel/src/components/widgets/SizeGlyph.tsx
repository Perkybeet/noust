import { cx } from "../../lib/cx";
import { SIZES } from "./model";
import type { SizeKey } from "./model";

/** The drawing's own units: the grid's width and four rows of it, inside a frame. */
const FRAME_W = 24;
const FRAME_H = 16;
const PAD = 1.5;

/**
 * A size drawn as its proportion: the width of the grid and four rows of it as an outline, and the
 * widget as the filled part of it. A letter says nothing about a shape; this says it at a glance, in the
 * menu of a widget, in the gallery and wherever a size is chosen (design 10.5). Decorative: the size's
 * name is always written beside it.
 */
export function SizeGlyph({ size, className }: { size: SizeKey; className?: string }) {
  const { w, h } = SIZES[size];
  const innerW = FRAME_W - PAD * 2;
  const innerH = FRAME_H - PAD * 2;
  return (
    <svg aria-hidden="true" focusable="false" data-size={size} viewBox={`0 0 ${String(FRAME_W)} ${String(FRAME_H)}`} className={cx("h-4 w-6 shrink-0", className)}>
      <rect x="0.5" y="0.5" width={FRAME_W - 1} height={FRAME_H - 1} rx="2.5" className="fill-none stroke-il-stroke" />
      <rect x={PAD} y={PAD} width={(w / 12) * innerW} height={(h / 4) * innerH} rx="1.2" className="fill-current" />
    </svg>
  );
}
