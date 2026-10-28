import { Link } from "@tanstack/react-router";
import { Fragment } from "react";
import type { ReactNode } from "react";

import { usePrimaryYielded } from "../components/page/primaryYield";
import { DemotePrimary } from "../components/ui/demotePrimary";
import { IconButton } from "../components/ui/IconButton";
import { ICONS } from "../components/ui/icons";
import { Menu } from "../components/ui/Menu";
import { Mono } from "../components/ui/Mono";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import { useDocumentTitle } from "./documentTitle";

/** One step of where a resource lives: a team, a project, an environment. */
export interface PlaceStep {
  /** Already translated, or a name the operator chose. */
  label: string;
  /** The list that step filters: a path of the console. */
  to: string;
  /** What narrows that list to the step: `{ team: "acme" }`. */
  search?: Record<string, unknown>;
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
  /** The resource's state, beside the title: a StatusPill (or AppStatePill). */
  status?: ReactNode;
  /**
   * What else belongs on the state's line, after it: "3 not applied", "Deploys paused". Chips that
   * open the thing they count; a banner on every tab says the same and costs the page its first
   * screen. Give one its room while it loads, as a `LinkTab` count does, so nothing shifts when
   * it arrives.
   */
  statusExtras?: ReactNode;
  /**
   * Where the resource lives, above the title, when the server has more than one place to tell
   * apart (a team, a project, an environment): `Acme / Web / production`, each step a link to the
   * list filtered to it. Give none where there is nothing to tell apart: a server nobody organised
   * draws no such line. Where the page is in the console is not this: it is the breadcrumbs of the top
   * bar, generated from the register, and the server is named there and in the second column, never
   * above a title (docs/DESIGN.md, NV-8).
   */
  place?: readonly PlaceStep[];
  /** The title is a system identifier (a domain, a unit, a database, a site): it is not translated. It is set like any title, in the display voice, and no longer in mono. */
  mono?: boolean;
  /** Facts about the resource on one line under the title: "Next.js · :3001 · shop.example.com". */
  meta?: ReactNode;
  /** Actions other than the primary one, before it. */
  secondaryActions?: ReactNode;
  /** The one primary action of the view (Button variant="primary"), after the others. */
  primaryAction?: ReactNode;
  /** MenuItems for everything else, behind a "More actions" button: the last control. */
  overflow?: ReactNode;
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
  status,
  statusExtras,
  place,
  mono = false,
  meta,
  secondaryActions,
  primaryAction,
  overflow,
  flush = false,
}: PageHeaderProps) {
  const t = useT();
  // While a save bar holds changes the view's primary is Save: this one is drawn as a secondary.
  const yielded = usePrimaryYielded();
  useDocumentTitle(title);
  const hasActions = actions !== undefined || secondaryActions !== undefined || primaryAction !== undefined || overflow !== undefined;
  return (
    <header className={cx("flex flex-wrap items-end justify-between gap-x-6 gap-y-4", !flush && "mb-8")}>
      {/* 240px before the actions wrap under: a phone keeps a lone menu button beside the title. */}
      <div className="min-w-0 flex-1 basis-60">
        {place !== undefined && place.length > 0 ? (
          <p className="mb-1 text-12 text-fg-muted">
            <span aria-label={t("common.pageHeader.place")} role="group" className="inline">
              {place.map((step, index) => (
                <Fragment key={`${step.to}-${step.label}`}>
                  {index > 0 ? <span aria-hidden="true"> / </span> : null}
                  <Link
                    to={step.to}
                    {...(step.search !== undefined ? { search: step.search as never } : {})}
                    className="rounded-chip hover:text-fg hover:underline hover:underline-offset-2"
                  >
                    <Mono tone="default">{step.label}</Mono>
                  </Link>
                </Fragment>
              ))}
            </span>
          </p>
        ) : null}
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <h1
            tabIndex={-1}
            data-page-title=""
            {...(mono ? { translate: "no" as const } : {})}
            className={cx(
              "min-w-0 break-words text-fg outline-none focus-visible:rounded-chip focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-focus",
              // A resource's own name (a domain, a server) is not translated and is set like every other title: in
              // the display voice. Mono is for logs, commands and code (docs/DESIGN.md Y-4).
              "display text-32",
            )}
          >
            {title}
          </h1>
          {status}
          {statusExtras}
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
          <DemotePrimary active={yielded}>
            {actions}
            {secondaryActions}
            {primaryAction}
          </DemotePrimary>
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
