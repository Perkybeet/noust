import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Server } from "lucide-react";
import { useId } from "react";
import type { ReactNode } from "react";

import { sessionQuery } from "../api/queries/auth";
import { activeJobsQuery } from "../api/queries/jobs";
import { machineQuery } from "../api/queries/system";
import { Logo } from "../components/brand/Logo";
import { Mono } from "../components/ui/Mono";
import { Skeleton } from "../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../components/ui/StatusPill";
import { isLocalOnlyPath, useCentral } from "../features/central/central";
import { useOrganization } from "../features/teams/useOrganization";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import { NODE_STATE, nodeStatus, useHasFleet, useReturnServer, useServerList } from "../nodes/servers";
import { useConsoleContext } from "../nodes/useNode";
import { FLEET_ITEM, NAV_GROUPS, SETTINGS_ITEM } from "./nav";
import type { ConsolePath, NavItem } from "./nav";

/** Failures worth seeing from anywhere: a red count next to the place that has them. */
function useFailureCounts(enabled: boolean): Partial<Record<ConsolePath, number>> {
  const { data } = useQuery({ ...machineQuery(), enabled });
  if (!data || !enabled) return {};
  return { "/apps": data.apps.failed, "/server": data.units.failed };
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

interface NavLinkProps {
  item: NavItem;
  failed?: number | undefined;
  /** Jobs running now, for the destination where they are followed. */
  running?: number | undefined;
  onNavigate?: (() => void) | undefined;
  /**
   * The server the link opens, when it is not the one on screen: from the fleet's or the
   * central's pages, a server's destinations lead back to the server the operator came from.
   * Undefined keeps whatever server the page is on.
   */
  node?: string | null | undefined;
}

function NavLink({ item, failed, running, onNavigate, node }: NavLinkProps) {
  const t = useT();
  const Icon = item.icon;
  return (
    <Link
      to={item.to}
      {...(node !== undefined ? { search: { node: node ?? undefined } } : {})}
      activeOptions={{ exact: item.to === "/", includeSearch: false }}
      {...(onNavigate ? { onClick: onNavigate } : {})}
      className={LINK}
    >
      {({ isActive }) => (
        <>
          {/* The fill of the current item is within 1.1 to 1.3 of the ground, which is not enough
              to tell it by (WCAG 1.4.11). The bar is: it is drawn in `text`, 3:1 or more against
              the ground, a hovered item and the active fill in both themes (tokens.test.ts). */}
          {isActive ? <span aria-hidden="true" data-nav-indicator="" className="absolute inset-y-1.5 left-0 w-0.5 rounded-pill bg-fg" /> : null}
          <Icon aria-hidden="true" className="size-icon-md shrink-0 text-fg-faint group-hover:text-fg-muted group-data-[status=active]:text-fg" />
          <span className="min-w-0 flex-1 truncate">{t(item.label)}</span>
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
function NavPending({ groups }: { groups: readonly (readonly NavItem[])[] }) {
  return (
    <>
      {groups.map((group) => (
        <ul key={group[0]?.to} aria-hidden="true" className="flex flex-col gap-px">
          {group.map((item) => (
            <li key={item.to} className="flex h-control-md items-center gap-2.5 px-2">
              <Skeleton className="size-icon-md shrink-0" />
              <Skeleton className="h-3 w-24" />
            </li>
          ))}
        </ul>
      ))}
    </>
  );
}

/** A group's heading: what the destinations under it are about. */
function GroupHeading({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p id={id} className="flex min-w-0 items-center gap-1.5 px-2 pb-1 text-12 font-medium text-fg-muted">
      {children}
    </p>
  );
}

/** The list of destinations, shared by the sidebar and the mobile menu. */
export function SidebarNav({ onNavigate, className }: { onNavigate?: () => void; className?: string }) {
  const t = useT();
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  const back = useReturnServer();
  const servers = useServerList();
  const { role } = useCentral();
  const serverId = useId();
  const org = useOrganization();
  // Whether Projects is offered is the server's answer, a moment after the shell is up: until
  // then the destinations are held as a skeleton (see NavPending).
  const settling = org.loading;
  // Projects are offered where there is something to organise, or someone who may start.
  const organised = org.available && (org.teamsMatter || org.scope.showProject || org.scope.showEnvironment || org.can("teams.manage"));

  // Whose destinations these are: the server on screen, or, from the fleet's and the
  // central's pages, the server the operator came from (null: none, a hub with no server yet).
  const onServer = context.kind === "server";
  const target = onServer ? { node: context.node, name: context.node ?? servers.hostname ?? "" } : back;
  const failures = useFailureCounts(onServer);
  const running = useRunningJobs(onServer);
  // A hub deploys nothing of its own: its overview and deployment pages only redirect to the
  // fleet, so they are not offered for it. A node's pages (/n/<server>/) are that node's.
  const groupsFor = (withOrganised: boolean) =>
    NAV_GROUPS.map((group) =>
      group.filter(
        (item) =>
          target !== null &&
          (item.when !== "organised" || withOrganised) &&
          (role !== "hub" || target.node !== null || (item.to !== "/" && !isLocalOnlyPath(item.to))),
      ),
    ).filter((group) => group.length > 0);
  const groups = groupsFor(organised);
  // What the skeleton stands for never changes while it is shown, or its own rows would move:
  // the destinations every server has. The links that depend on the teams come with the links.
  const pending = groupsFor(false);
  const record = target?.node === null || target === null ? undefined : servers.nodes.find((candidate) => candidate.name === target.node);
  const trouble = record === undefined ? null : NODE_STATE[nodeStatus(record)];
  // Off a server's own pages, its destinations say which server they open.
  const linkNode = onServer ? undefined : (target?.node ?? null);

  return (
    <nav aria-label={t("nav.landmarks.main")} aria-busy={settling ? true : undefined} className={cx("flex flex-col gap-5", className)}>
      {hasFleet ? (
        // Every server at once, apart from any one server's destinations below.
        <ul aria-label={t("nav.groups.fleet")} className="flex flex-col gap-px">
          <li>
            <NavLink item={FLEET_ITEM} onNavigate={onNavigate} />
          </li>
        </ul>
      ) : null}
      {groups.length > 0 && target !== null ? (
        <div className="flex flex-col gap-5">
          {hasFleet ? (
            // On a fleet every destination below is one server's: its name heads them, in mono.
            <GroupHeading id={serverId}>
              <Server aria-hidden="true" className="size-icon-sm shrink-0 text-fg-faint" />
              <span className="sr-only">{t("nav.groups.server")}</span>{" "}
              <Mono tone="default" truncate className="min-w-0">
                {target.name}
              </Mono>
              {trouble !== null && trouble.state !== "running" ? (
                <>
                  {" "}
                  <span className={cx("inline-flex shrink-0 items-center gap-1", stateTextClass(trouble.state))}>
                    <StatusGlyph state={trouble.state} size={10} />
                    <span className="sr-only">{t(trouble.label)}</span>
                  </span>
                </>
              ) : null}
            </GroupHeading>
          ) : null}
          {settling ? (
            <NavPending groups={pending} />
          ) : (
            groups.map((group) => (
              <ul key={group[0]?.to} {...(hasFleet ? { "aria-labelledby": serverId } : {})} className="flex flex-col gap-px">
                {group.map((item) => (
                  <li key={item.to}>
                    <NavLink item={item} failed={failures[item.to]} running={item.to === "/activity" ? running : undefined} onNavigate={onNavigate} node={linkNode} />
                  </li>
                ))}
              </ul>
            ))
          )}
        </div>
      ) : null}
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
          <NavLink item={SETTINGS_ITEM} onNavigate={onNavigate} node={node} />
        </li>
      </ul>
      {version !== undefined ? (
        <p className="px-2 text-12 text-fg-muted">{t.rich("shell.version", { version: <Mono tone="muted">{version}</Mono> })}</p>
      ) : null}
    </div>
  );
}

/** The permanent sidebar on wide screens. Narrow screens get the same lists in a drawer. */
export function Sidebar() {
  const t = useT();
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
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-4 scroll-thin">
        <SidebarNav />
      </div>
      <div className="shrink-0 border-t border-border px-3 py-3">
        <SidebarFooter />
      </div>
    </aside>
  );
}
