import { LogoTile } from "../../components/ui/LogoTile";
import type { LogoSlug } from "../../components/ui/LogoTile";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/**
 * Which packaged logo stands for an application type, for the types that have a mark of their own. A
 * type without one (static, Go, Ruby, a monorepo) draws no logo: the console does not invent a brand for
 * it, and a tile behind its own icon would say nothing (docs/DESIGN.md, IL-2).
 */
const TYPE_LOGOS: Readonly<Record<string, LogoSlug>> = {
  nodejs: "nodedotjs",
  nextjs: "nextdotjs",
  python: "python",
  astro: "astro",
  php: "php",
  "php-fpm": "php",
  "docker-compose": "docker",
  container: "docker",
};

/** The logo of an application type, or null when it has none. */
export function typeLogo(type: string | null | undefined): LogoSlug | null {
  return type === null || type === undefined ? null : (TYPE_LOGOS[type] ?? null);
}

/**
 * What an application is made of, at a glance, before its name: the logo of its type in a tile, or the
 * console's own icon of an application, bare, in the same box so a column of names lines up whether or not
 * each has a logo. Decoration: the type is also said in words in the column or the header beside it.
 */
export function AppTypeMark({ type, size = "sm" }: { type: string | null | undefined; size?: "sm" | "md" | "lg" }) {
  const logo = typeLogo(type);
  if (logo !== null) return <LogoTile logo={logo} size={size} />;
  return (
    <span aria-hidden="true" className="inline-flex size-8 shrink-0 items-center justify-center text-fg-faint">
      <OBJECT_ICONS.app className="size-icon-md" />
    </span>
  );
}
