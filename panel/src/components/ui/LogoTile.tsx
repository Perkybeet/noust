import { useState } from "react";

import { cx } from "../../lib/cx";
import { LOGOS } from "./logos/logoData";
import type { LogoSlug } from "./logos/logoData";

export type { LogoSlug } from "./logos/logoData";

export interface LogoTileProps {
  /** Which logo: one of the marks packaged with the console (`logos/logoData.ts`). */
  logo: LogoSlug;
  size?: "sm" | "md" | "lg";
  /** Names the logo when it says what the thing is and no text beside it does. Without it the logo is decoration. */
  label?: boolean;
  className?: string;
}

const SIZES = {
  sm: { tile: "size-8 rounded-control", mark: "size-5" },
  md: { tile: "size-10 rounded-control", mark: "size-6" },
  lg: { tile: "size-14 rounded-card", mark: "size-8" },
} as const;

/** The brand colour of each logo, read through a custom property so one rule serves both themes. */
function brandColour(logo: LogoSlug): string {
  const { light, dark } = LOGOS[logo];
  return `light-dark(#${light}, #${dark})`;
}

/**
 * A real logo of an application or an engine, in a carved tile. It is the only tile in the
 * console: a tile behind an icon of the console's own is a tic of templated interfaces, and an
 * icon says nothing a logo does (docs/DESIGN.md 2.11). The marks are packaged (no request leaves
 * the console to draw one), come from Simple Icons under CC0, and are shown in their brand's
 * colour, which is the one colour on screen that is neither a state nor an action: the brand's own,
 * as the hull of Noust's is. The brands are their owners' (`logos/NOTICE.md`).
 *
 * The tile is not a button. A logo that is chosen sits inside what is (a recipe card).
 */
export function LogoTile({ logo, size = "md", label = false, className }: LogoTileProps) {
  const data = LOGOS[logo];
  const sizes = SIZES[size];
  return (
    <span
      data-slot="logo-tile"
      data-logo={logo}
      className={cx("elev-carved-fine inline-flex shrink-0 items-center justify-center bg-bg-sunken", sizes.tile, className)}
    >
      <svg
        viewBox="0 0 24 24"
        focusable="false"
        className={sizes.mark}
        // The brand's colour is data of the logo (logoData.ts), not a token: it is set through the CSSOM, which the policy allows.
        style={{ color: brandColour(logo) }}
        {...(label ? { role: "img", "aria-label": data.title } : { "aria-hidden": true })}
      >
        <path d={data.path} fill="currentColor" />
      </svg>
    </span>
  );
}

export interface ImageLogoTileProps {
  /** Where the logo is served from (a recipe's `logo`), or null when it has none: the letters stand in. */
  src: string | null;
  /** Two letters of the thing's name, for when there is no logo or it did not load. */
  fallback: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

/**
 * The logo of an application the console does not package (a recipe's, served as a file of its own by the
 * server, an SVG held to a closed list of elements or a PNG) in the same carved tile as a packaged mark. It is
 * shown through `<img>`, never inlined, so it needs nothing the Content Security Policy forbids. Decoration:
 * the name of what it is is always beside it. When there is no logo, or it fails to load, two letters stand in,
 * in the same box, so a card never changes size and nothing is drawn that is not a real mark or an honest gap.
 */
export function ImageLogoTile({ src, fallback, size = "md", className }: ImageLogoTileProps) {
  const sizes = SIZES[size];
  // The address a failure was seen for: a different logo in the same place gets its own try.
  const [failedFor, setFailedFor] = useState<string | null>(null);
  const drawn = src !== null && failedFor !== src;
  return (
    <span data-slot="logo-tile" className={cx("elev-carved-fine inline-flex shrink-0 items-center justify-center bg-bg-sunken", sizes.tile, className)}>
      {drawn ? (
        <img src={src} alt="" loading="lazy" decoding="async" onError={() => setFailedFor(src)} className={cx("object-contain", sizes.mark)} />
      ) : (
        <span aria-hidden="true" translate="no" className="text-13 font-medium text-fg-muted">
          {fallback}
        </span>
      )}
    </span>
  );
}
