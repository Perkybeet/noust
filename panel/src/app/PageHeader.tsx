import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

import { IconButton } from "../components/ui/IconButton";
import { ICONS } from "../components/ui/icons";
import { Menu } from "../components/ui/Menu";
import { Mono } from "../components/ui/Mono";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import { useNamedServer } from "../nodes/pageServer";
import { useDocumentTitle } from "./documentTitle";

export interface Breadcrumb {
  label: string;
  /** A path of the console, with params already filled in. */
  to: string;
}

export interface PageHeaderProps {
  title: string;
  /** One sentence of prose: what this page is for. Facts about a resource go in `meta`. */
  description?: ReactNode;
  /**
   * The page's actions, right-aligned, the most important last: the 3.0 form. New pages use
   * `secondaryActions`, `primaryAction` and `overflow`, which put them in the system's order.
   */
  actions?: ReactNode;
  breadcrumbs?: readonly Breadcrumb[];
  /** The resource's state, beside the title: a StatusPill (or AppStatePill). */
  status?: ReactNode;
  /** Sets the title in mono, for a system identifier: a domain, a unit, a database, a site. */
  mono?: boolean;
  /** Facts about the resource on one line under the title: "Next.js · :3001 · shop.example.com". */
  meta?: ReactNode;
  /** Actions other than the primary one, before it. */
  secondaryActions?: ReactNode;
  /** The one primary action of the view (Button variant="primary"), after the others. */
  primaryAction?: ReactNode;
  /** MenuItems for everything else, behind a "More actions" button: the last control. */
  overflow?: ReactNode;
  /** The server this page is about, said above the title when the console manages several. */
  server?: string | null;
  /** No bottom margin: the page template spaces what follows the header. */
  flush?: boolean;
}

/**
 * The start of every page: where you are, what this page is for, and what you can do here.
 * The h1 takes focus after a navigation, so a screen reader hears the new page's name first.
 */
export function PageHeader({
  title,
  description,
  actions,
  breadcrumbs,
  status,
  mono = false,
  meta,
  secondaryActions,
  primaryAction,
  overflow,
  server,
  flush = false,
}: PageHeaderProps) {
  const t = useT();
  // On a fleet the shell names the server of every per-server page, this one's included.
  const named = useNamedServer(server);
  useDocumentTitle(title);
  const hasActions = actions !== undefined || secondaryActions !== undefined || primaryAction !== undefined || overflow !== undefined;
  return (
    <header className={cx("flex flex-wrap items-end justify-between gap-x-6 gap-y-4", !flush && "mb-8")}>
      {/* 240px before the actions wrap under: a phone keeps a lone menu button beside the title. */}
      <div className="min-w-0 flex-1 basis-60">
        {named !== null ? (
          <p className="mb-1 text-12 text-fg-muted">{t.rich("common.pageHeader.server", { server: <Mono tone="default">{named}</Mono> })}</p>
        ) : null}
        {breadcrumbs && breadcrumbs.length > 0 ? (
          <nav aria-label={t("shell.pageHeader.breadcrumb")} className="mb-2">
            <ol className="flex flex-wrap items-center gap-1 text-13 text-fg-muted">
              {breadcrumbs.map((crumb) => (
                <li key={crumb.to} className="flex items-center gap-1">
                  <Link
                    to={crumb.to}
                    className="rounded-chip hover:text-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
                  >
                    {crumb.label}
                  </Link>
                  <ChevronRight aria-hidden="true" className="size-icon-sm text-fg-faint" />
                </li>
              ))}
            </ol>
          </nav>
        ) : null}
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <h1
            tabIndex={-1}
            data-page-title=""
            {...(mono ? { translate: "no" as const } : {})}
            className={cx(
              "min-w-0 text-24 break-words text-fg outline-none focus-visible:rounded-chip focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-focus",
              mono ? "mono font-medium" : "title",
            )}
          >
            {title}
          </h1>
          {status}
        </div>
        {meta !== undefined ? (
          <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-13 text-fg-muted">{meta}</div>
        ) : null}
        {description !== undefined ? (
          <p className="mt-1.5 max-w-measure text-14 text-pretty text-fg-muted">{description}</p>
        ) : null}
      </div>
      {hasActions ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {actions}
          {secondaryActions}
          {primaryAction}
          {overflow !== undefined ? (
            <Menu align="end" trigger={<IconButton variant="secondary" label={t("common.pageHeader.moreActions")} icon={<ICONS.more />} />}>
              {overflow}
            </Menu>
          ) : null}
        </div>
      ) : null}
    </header>
  );
}
