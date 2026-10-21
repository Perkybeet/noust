import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import { useMemo } from "react";

import { sessionQuery } from "../api/queries/auth";
import { activeJobsQuery } from "../api/queries/jobs";
import { Logo } from "../components/brand/Logo";
import { Mono } from "../components/ui/Mono";
import { Skeleton } from "../components/ui/Skeleton";
import { StatusGlyph } from "../components/ui/StatusPill";
import { LG_UP, useMediaQuery } from "../components/ui/useMediaQuery";
import { isLocalOnlyPath, useCentral } from "../features/central/central";
import { useAttention } from "../features/inbox/useAttention";
import { useOrganised } from "../features/teams/useOrganised";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import { ScopeSelector } from "../nodes/ScopeSelector";
import { useHasFleet, useReturnServer } from "../nodes/servers";
import { useConsoleContext } from "../nodes/useNode";
import { iconOf } from "../features/nav/icons";
import { dest, isUnder, litBy, sidebarFor } from "./nav";
import { useNavScope } from "./useNavScope";
import type { ConsolePath, Dest, Scope } from "./nav";

/**
 * What is worth seeing from anywhere, beside the place that has it: a red count of what failed and
 * an amber one of what is about to. They are counted from the same list the inbox shows (NV-16), so
 * the bell and the bar never disagree.
 */
function useCounts(enabled: boolean): { failed: Partial<Record<ConsolePath, number>>; expiring: Partial<Record<ConsolePath, number>> } {
  const { counters } = useAttention();
  if (!enabled) return { failed: {}, expiring: {} };
  return {
    failed: { "/apps": counters.appsFailed, "/server": counters.unitsFailed, "/domains": counters.certificatesExpired },
    expiring: { "/domains": counters.certificates - counters.certificatesExpired },
  };
}

/**
 * What is running now (a deploy, a backup, an update): the jobs the server has queued or is running,
 * read once and kept current by the `job` events, so the count is true from any page (checklist
 * M-28). Shown beside Activity, where each of them can be followed.
 */
function useRunningJobs(enabled: boolean): number {
  const { data } = useQuery({ ...activeJobsQuery(), enabled, retry: false });
  return enabled && data !== undefined ? data.jobs.length : 0;
}

const LINK = cx(
  "group relative flex h-control-md items-center gap-2.5 rounded-control px-2 text-13 text-fg-muted",
  "transition-colors duration-(--duration-fast) ease-out",
  "hover:bg-surface-hover hover:text-fg",
  "focus-visible:-outline-offset-2",
  "data-[status=active]:bg-surface-active data-[status=active]:font-medium data-[status=active]:text-fg",
);

/** The addresses of an area that are only its own (the overview of a server or of the fleet): nothing beneath them lights it. */
const EXACT: ReadonlySet<string> = new Set(["/", "/fleet"]);

interface NavLinkProps {
  entry: Dest;
  failed?: number | undefined;
  /** Things about to need attention (a certificate close to expiring), in amber. */
  expiring?: number | undefined;
  /** Jobs running now, for the destination where they are followed. */
  running?: number | undefined;
  onNavigate?: (() => void) | undefined;
  /**
   * The server the link opens, when it is not the one on screen: from the fleet's or the
   * central's pages, Settings leads back to the server the operator came from. Undefined keeps
   * whatever server the page is on.
   */
  node?: string | null | undefined;
}

function NavLink({ entry, failed, expiring, running, onNavigate, node }: NavLinkProps) {
  const t = useT();
  const Icon = iconOf(entry);
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  // The screens beneath an area that live at another address (the recipes are Applications') light it too.
  const extra = useMemo(() => litBy(entry), [entry]);
  const lit = extra.some((prefix) => isUnder(pathname, prefix));
  return (
    <Link
      to={entry.to}
      {...(node !== undefined ? { search: { node: node ?? undefined } } : {})}
      activeOptions={{ exact: EXACT.has(entry.to), includeSearch: false }}
      {...(onNavigate ? { onClick: onNavigate } : {})}
      {...(lit ? { "aria-current": "page" as const } : {})}
      className={cx(LINK, lit && "bg-surface-active font-medium text-fg")}
    >
      {({ isActive }) => (
        <>
          {/* The fill of the current item is within 1.1 to 1.3 of the ground, which is not enough
              to tell it by (WCAG 1.4.11). The bar is: it is drawn in `text`, 3:1 or more against
              the ground, a hovered item and the active fill in both themes (tokens.test.ts). */}
          {isActive || lit ? <span aria-hidden="true" data-nav-indicator="" className="absolute inset-y-1.5 left-0 w-0.5 rounded-pill bg-fg" /> : null}
          <Icon aria-hidden="true" className={cx("size-icon-md shrink-0 text-fg-faint group-hover:text-fg-muted group-data-[status=active]:text-fg", lit && "text-fg")} />
          <span className="min-w-0 flex-1 truncate">{t(entry.label)}</span>
          {/* Spaces between the parts keep the accessible name "Server 1 failed"; the visible
              number is the one inside that sentence, read once. */}
          {failed !== undefined && failed > 0 ? " " : null}
          {failed !== undefined && failed > 0 ? (
            <span className="inline-flex items-center gap-1 text-12 font-medium text-fail">
              <StatusGlyph state="failed" size={10} />
              <span aria-hidden="true" className="tabular-nums">
                {failed}
              </span>
              <span className="sr-only">{t("nav.failed", { count: failed })}</span>
            </span>
          ) : null}
          {expiring !== undefined && expiring > 0 ? " " : null}
          {expiring !== undefined && expiring > 0 ? (
            <span className="inline-flex items-center gap-1 text-12 font-medium text-warn">
              <StatusGlyph state="warning" size={10} />
              <span aria-hidden="true" className="tabular-nums">
                {expiring}
              </span>
              <span className="sr-only">{t("nav.expiring", { count: expiring })}</span>
            </span>
          ) : null}
          {running !== undefined && running > 0 ? " " : null}
          {running !== undefined && running > 0 ? (
            <span className="inline-flex items-center gap-1 text-12 font-medium text-warn">
              <StatusGlyph state="deploying" size={10} />
              <span aria-hidden="true" className="tabular-nums">
                {running}
              </span>
              <span className="sr-only">{t("nav.running", { count: running })}</span>
            </span>
          ) : null}
        </>
      )}
    </Link>
  );
}

/**
 * The destinations while the sidebar has not learned which there are: a row of the height of a
 * link for each, in the groups they fall in. Only when it knows are the links drawn, so the
 * Projects link, which a server offers once its teams are read, never pushes the rows under it
 * down: what arrives is new, and nothing that was on screen moves.
 */
function NavPending({ groups }: { groups: readonly (readonly Dest[])[] }) {
  return (
    <>
      {groups.map((group) => (
        <ul key={group[0]?.to} aria-hidden="true" className="flex flex-col gap-px">
          {group.map((entry) => (
            <li key={entry.to} className="flex h-control-md items-center gap-2.5 px-2">
              <Skeleton className="size-icon-md shrink-0" />
              <Skeleton className="h-3 w-24" />
            </li>
          ))}
        </ul>
      ))}
    </>
  );
}

/**
 * The list of destinations, shared by the sidebar and the mobile menu. It follows the scope the
 * selector chose (NV-10): a server's destinations while a server is selected, the fleet's own
 * seven while "All servers" or "This central" is, never the last server's under another's name.
 */
export function SidebarNav({ onNavigate, className }: { onNavigate?: () => void; className?: string }) {
  const t = useT();
  const context = useConsoleContext();
  const { role } = useCentral();
  // Whether Projects is offered is the server's answer, a moment after the shell is up: until
  // then the destinations are held as a skeleton (see NavPending).
  const { organised, settling } = useOrganised();

  const scope: Scope = useNavScope();
  const onServer = scope === "server";
  const counts = useCounts(onServer);
  const running = useRunningJobs(onServer);
  // A hub deploys nothing of its own: its overview and deployment pages only redirect to the
  // fleet, so they are not offered for it. A node's pages (/n/<server>/) are that node's.
  const onHub = role === "hub" && context.kind === "server" && context.node === null;
  const groupsFor = (withOrganised: boolean): Dest[][] =>
    sidebarFor(scope, { when: { organised: withOrganised } })
      .map((group) => group.filter((entry) => !onHub || (entry.to !== "/" && !isLocalOnlyPath(entry.to))))
      .filter((group) => group.length > 0);
  const groups = groupsFor(organised);
  // What the skeleton stands for never changes while it is shown, or its own rows would move:
  // the destinations every server has. The links that depend on the teams come with the links.
  const pending = groupsFor(false);

  return (
    <nav aria-label={t("nav.landmarks.main")} aria-busy={settling && onServer ? true : undefined} className={cx("flex flex-col gap-5", className)}>
      {settling && onServer ? (
        <NavPending groups={pending} />
      ) : (
        groups.map((group) => (
          <ul key={group[0]?.to} className="flex flex-col gap-px">
            {group.map((entry) => (
              <li key={entry.to}>
                <NavLink entry={entry} failed={counts.failed[entry.to]} expiring={counts.expiring[entry.to]} running={entry.to === "/activity" ? running : undefined} onNavigate={onNavigate} />
              </li>
            ))}
          </ul>
        ))
      )}
    </nav>
  );
}

/** Settings and the installed version, pinned under the destinations. */
export function SidebarFooter({ onNavigate }: { onNavigate?: () => void }) {
  const t = useT();
  const context = useConsoleContext();
  const back = useReturnServer();
  const { data: version } = useQuery({ ...sessionQuery(), select: (session) => session.version });
  // The settings of the server on screen; off one, of the server the operator came from.
  const node = context.kind === "server" ? undefined : (back?.node ?? null);
  return (
    <div className="flex flex-col gap-2">
      <ul>
        <li>
          <NavLink entry={dest("settings")} onNavigate={onNavigate} node={node} />
        </li>
      </ul>
      {version !== undefined ? <p className="px-2 text-12 text-fg-muted">{t.rich("shell.version", { version: <Mono tone="muted">{version}</Mono> })}</p> : null}
    </div>
  );
}

/**
 * The permanent sidebar on wide screens: the logo, the scope selector, the destinations of the
 * scope and Settings. Narrow screens get the same lists in a drawer, and the selector in the top
 * bar (a menu inside a drawer is not on screen, and the server's name always must be).
 */
export function Sidebar() {
  const t = useT();
  const wide = useMediaQuery(LG_UP);
  const hasFleet = useHasFleet();
  const context = useConsoleContext();
  // Nothing of the selector exists on a lone server with no nodes (NV-10), not even its room.
  const selector = wide && (hasFleet || (context.kind === "server" && context.node !== null));
  return (
    <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r border-border lg:flex">
      <div className="flex h-14 shrink-0 items-center border-b border-border px-5">
        <Link to="/" aria-label={t("shell.sidebarOverview")} className="-mx-1.5 rounded-control px-1.5 py-1">
          <span className="inline-flex items-center gap-2">
            <Logo variant="wordmark" height={18} decorative />
            <span className="text-14 leading-none font-medium text-fg-muted">{t("shell.consoleProduct")}</span>
          </span>
        </Link>
      </div>
      {selector ? (
        <div className="shrink-0 px-3 pt-3">
          <ScopeSelector placement="sidebar" />
        </div>
      ) : null}
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-4 scroll-thin">
        <SidebarNav />
      </div>
      <div className="shrink-0 border-t border-border px-3 py-3">
        <SidebarFooter />
      </div>
    </aside>
  );
}
