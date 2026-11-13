import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { OBJECT_ICONS } from "../ui/objectIcons";
import { textLinkClassName } from "../ui/TextLink";

/** Whose a thing is: an application of Noust, a database engine of Noust, Noust itself, or nobody (Noust did not create it). */
export type OwnerKind = "app" | "database" | "noust" | "nobody";

/** Where an owner leads, as the router writes it. The kit never knows the routes: the page says where. */
export interface OwnerTarget {
  to: string;
  params?: Record<string, string>;
  search?: Record<string, string | undefined>;
}

export interface OwnerMarkProps {
  kind: OwnerKind;
  /** The application's domain, the engine's key (`postgresql@pg16`), or what Noust holds; empty for nobody. */
  subject: string;
  /** Where the owner's page is. Without it the owner is text: Noust itself and nobody lead nowhere. */
  target?: OwnerTarget | undefined;
  /**
   * `mark`: the icon of the owner and its name, one line, for a table cell. `sentence`: "Of the application shop.example.com",
   * for the line under a title or in a dialog, with the owner as the link.
   */
  variant?: "mark" | "sentence";
  className?: string;
}

const ICON_OF = { app: OBJECT_ICONS.app, database: OBJECT_ICONS.database, noust: OBJECT_ICONS.noust, nobody: null } as const;

/** The link a target makes: typed loosely on purpose, since the kit carries no route table. */
function OwnerLink({ target, className, children }: { target: OwnerTarget; className: string; children: ReactNode }) {
  return (
    <Link to={target.to} params={target.params as never} search={target.search as never} className={className}>
      {children}
    </Link>
  );
}

/**
 * Who owns a thing, always in view and a link where there is somewhere to go: the icon of the owner's kind, its name in the
 * interface typeface and not translated (a name is not code), and, for a screen reader, what kind of owner it is. Docker is
 * Noust's own now (an application's container, a database engine's, Noust's own, and what nobody of Noust made), so every list
 * says whose each thing is in the same place and in the same way, and "nobody" is said in words, never as a dash.
 */
export function OwnerMark({ kind, subject, target, variant = "mark", className }: OwnerMarkProps) {
  const t = useT();
  const named = kind === "app" || kind === "database";
  const name = named ? subject : kind === "noust" ? t("docker.owner.noust") : t("docker.owner.nobody");
  const kindWord = kind === "app" ? t("docker.owner.app") : kind === "database" ? t("docker.owner.database") : "";

  if (variant === "sentence") {
    const link =
      target !== undefined && named ? (
        <OwnerLink target={target} className={textLinkClassName("inline")}>
          <span translate="no">{subject}</span>
        </OwnerLink>
      ) : (
        <span translate="no">{subject}</span>
      );
    const sentence =
      kind === "app"
        ? t.rich("docker.owner.line.app", { name: link })
        : kind === "database"
          ? t.rich("docker.owner.line.database", { name: link })
          : kind === "noust"
            ? t("docker.owner.line.noust")
            : t("docker.owner.line.nobody");
    // A span, never a paragraph: it goes inside one (the line under a page's title is a paragraph already).
    return <span className={cx("min-w-0", className)}>{sentence}</span>;
  }

  const Icon = ICON_OF[kind];
  const content = (
    <>
      {Icon !== null ? <Icon aria-hidden="true" className="size-icon-sm shrink-0 text-fg-faint" /> : null}
      <span translate={named ? "no" : undefined} className={cx("min-w-0 truncate", kind === "nobody" && "text-fg-muted")}>
        {name}
      </span>
      {kindWord !== "" ? <span className="sr-only">{`, ${kindWord}`}</span> : null}
    </>
  );
  return target !== undefined ? (
    <OwnerLink target={target} className={cx("inline-flex min-w-0 max-w-full items-center gap-1.5", textLinkClassName("ui"), className)}>
      {content}
    </OwnerLink>
  ) : (
    <span className={cx("inline-flex min-w-0 max-w-full items-center gap-1.5 text-13 text-fg", className)}>{content}</span>
  );
}
