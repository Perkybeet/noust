import { useState } from "react";

import { recipeLogoSrc } from "../../api/queries/recipes";
import { cx } from "../../lib/cx";

/** Two letters of a recipe's name, for the tile shown when it has no logo or the logo did not load. */
export function monogram(title: string): string {
  const letters = title.replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  if (letters === "") return "?";
  const words = letters.split(" ");
  const [first = "", second = ""] = words;
  const pair = words.length > 1 ? `${first.charAt(0)}${second.charAt(0)}` : letters.slice(0, 2);
  return pair.toUpperCase();
}

export interface RecipeLogoProps {
  /** Where the logo is served (`logo` of the recipe), or null when it has none. */
  logo: string | null | undefined;
  /** The recipe's title, for the monogram. */
  title: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

const SIZES = { sm: "size-6", md: "size-10", lg: "size-16" } as const;
const PIXELS = { sm: 24, md: 40, lg: 64 } as const;

/**
 * A recipe's logo, as the image the server serves: an SVG held to a closed list of elements, so
 * it needs nothing the Content Security Policy forbids and is shown through `<img>`, never
 * inlined. It is decoration beside the recipe's name, which always names it. When there is no
 * logo, or it fails to load, a tile with two letters stands in, in the same box, so a card never
 * changes size.
 */
export function RecipeLogo({ logo, title, size = "md", className }: RecipeLogoProps) {
  const src = logo !== null && logo !== undefined && logo !== "" ? recipeLogoSrc(logo) : null;
  // The address a failure was seen for: a different recipe in the same place gets its own try.
  const [failedFor, setFailedFor] = useState<string | null>(null);
  const box = cx("shrink-0 rounded-control", SIZES[size], className);
  if (src === null || failedFor === src) {
    return (
      <span
        aria-hidden="true"
        translate="no"
        className={cx(box, "flex items-center justify-center border border-border bg-bg-sunken font-medium text-fg-muted", size === "sm" ? "text-12" : size === "lg" ? "text-18" : "text-13")}
      >
        {monogram(title)}
      </span>
    );
  }
  return (
    <img
      src={src}
      alt=""
      width={PIXELS[size]}
      height={PIXELS[size]}
      loading="lazy"
      decoding="async"
      onError={() => setFailedFor(src)}
      className={cx(box, "object-contain")}
    />
  );
}
