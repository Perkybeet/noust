import { cx } from "../../lib/cx";

export interface LayoutGlyphProps {
  /** How many bars the drawing has at the side: the double bar (a bar of areas and a column) or one bar. */
  bars: 1 | 2;
  className?: string;
}

/**
 * The drawing of a window with its navigation at the side, one bar or two: what the choice between the
 * presentations of the navigation looks like, so it is chosen by seeing it and not by reading about it.
 * Drawn the way the set is (24 by 24, a stroke of 2, round ends, the colour of its text) and decoration:
 * whoever places it names what it is.
 */
export function LayoutGlyph({ bars, className }: LayoutGlyphProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={cx("size-icon-md shrink-0", className)}
    >
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d={bars === 2 ? "M8 4v16M13 4v16" : "M9 4v16"} />
    </svg>
  );
}
