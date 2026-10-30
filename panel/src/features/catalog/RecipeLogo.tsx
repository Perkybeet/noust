import { recipeLogoSrc } from "../../api/queries/recipes";
import { ImageLogoTile } from "../../components/ui/LogoTile";

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

/**
 * A recipe's logo, as the file the server serves (`GET /api/recipes/{name}/logo`), in the kit's tile for a
 * logo (`ImageLogoTile`): shown through `<img>`, never inlined, and decoration beside the recipe's name, which
 * always names it. When there is no logo, or it fails to load, two letters stand in, in the same box.
 */
export function RecipeLogo({ logo, title, size = "md", className }: RecipeLogoProps) {
  const src = logo !== null && logo !== undefined && logo !== "" ? recipeLogoSrc(logo) : null;
  return <ImageLogoTile src={src} fallback={monogram(title)} size={size} {...(className !== undefined ? { className } : {})} />;
}
