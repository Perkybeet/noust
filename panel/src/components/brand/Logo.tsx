import { cx } from "../../lib/cx";
import iconMonoUrl from "../../assets/brand/noust-icon-mono.svg?url&no-inline";
import iconUrl from "../../assets/brand/noust-icon.svg?url&no-inline";
import markMonoUrl from "../../assets/brand/noust-mark-mono.svg?url&no-inline";
import markUrl from "../../assets/brand/noust-mark.svg?url&no-inline";
import wordmarkMonoUrl from "../../assets/brand/noust-wordmark-mono.svg?url&no-inline";
import wordmarkUrl from "../../assets/brand/noust-wordmark.svg?url&no-inline";

export type LogoVariant = "mark" | "icon" | "wordmark";

interface Artwork {
  /** The drawing's group id inside the file, which `<use>` points at. */
  id: string;
  width: number;
  height: number;
  brand: string;
  mono: string;
}

// The hand-written files in assets/brand are the only copy of the drawing. They are referenced
// with <use> rather than an <img> so that their ink, drawn in currentColor, takes the text
// colour of whichever theme the logo sits in; an image cannot see the page's theme. A same-origin
// <use> is markup, not an inline style or a string-to-DOM sink, so the strict CSP is untouched.
// `no-inline` because <use> cannot reference a data: URL, which is what Vite makes of a small file.
const ARTWORK: Record<LogoVariant, Artwork> = {
  mark: { id: "noust-mark", width: 140, height: 36, brand: markUrl, mono: markMonoUrl },
  icon: { id: "noust-icon", width: 32, height: 32, brand: iconUrl, mono: iconMonoUrl },
  wordmark: { id: "noust-wordmark", width: 254, height: 36, brand: wordmarkUrl, mono: wordmarkMonoUrl },
};

export interface LogoProps {
  variant?: LogoVariant;
  /** Rendered height in CSS pixels; the width follows the drawing's proportions. */
  height?: number;
  /** One colour throughout, the hull included: for contexts that allow no violet. */
  mono?: boolean;
  /**
   * Hides the logo from assistive technology. Set it when visible text next to the logo, or the
   * accessible name of the link around it, already says what it is.
   */
  decorative?: boolean;
  className?: string;
}

/** The Noust logo: the mark, the square icon, or the mark with the "noust" wordmark. */
export function Logo({ variant = "mark", height = 24, mono = false, decorative = false, className }: LogoProps) {
  const art = ARTWORK[variant];
  const width = Math.round((height * art.width * 100) / art.height) / 100;
  return (
    <svg
      viewBox={`0 0 ${art.width} ${art.height}`}
      width={width}
      height={height}
      className={cx("shrink-0 text-fg", className)}
      {...(decorative ? { "aria-hidden": true } : { role: "img", "aria-label": "Noust" })}
    >
      <use href={`${mono ? art.mono : art.brand}#${art.id}`} />
    </svg>
  );
}
