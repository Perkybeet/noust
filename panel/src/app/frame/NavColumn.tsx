import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useRouterState } from "@tanstack/react-router";
import type { LucideIcon } from "lucide-react";
import { useId, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { appQuery } from "../../api/queries/apps";
import { sessionQuery } from "../../api/queries/auth";
import { SETTINGS_FILTER_FROM, answersFilter, groupNavItems } from "../../components/page/SettingsLayout";
import { Input } from "../../components/ui/Input";
import { Kbd } from "../../components/ui/Kbd";
import { Skeleton } from "../../components/ui/Skeleton";
import { SlidingIndicator } from "../../components/ui/SlidingIndicator";
import { ICONS } from "../../components/ui/icons";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { isAdopted } from "../../features/app/adopted";
import { SERVER_CAPABILITY, securityOverviewQuery, summaryQuery } from "../../features/server/queries";
import { useSettingsItems } from "../../features/settings/useSettingsItems";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { useNodeCapability } from "../../nodes/capability";
import { useHasFleet } from "../../nodes/servers";
import { useNode } from "../../nodes/useNode";
import { appColumn, currentItem, serverColumn } from "../columns";
import type { ColumnGroup, ColumnKind } from "../columns";
import { usePageServerName } from "./usePageServerName";

/** One link of the column, whatever resource it is the sections of. */
interface ListItem {
  key: string;
  to: string;
  params?: Record<string, string>;
  search?: Record<string, unknown>;
  label: string;
  icon?: LucideIcon;
  exact?: boolean;
  /** A count after the name, `null` while it loads: its room is kept, so the list does not change when it arrives. */
  count?: number | null;
}

interface ListGroup {
  key: string;
  /** What the group is for ("Use", "Danger"), or whose it is in Settings. */
  heading?: ReactNode;
  /** The block above the group where the same groups exist more than once (a server's name on a fleet). */
  scope?: ReactNode;
  danger?: boolean;
  items: ListItem[];
}

// The current section is the raised piece under it (SlidingIndicator), which slides to the one chosen;
// the others rise when the pointer is over them.
const ITEM =
  "group elev-lift relative flex h-8 items-center gap-2.5 rounded-control px-2.5 text-13 text-fg-muted " +
  "hover:bg-surface-raised hover:text-fg focus-visible:-outline-offset-2 " +
  "aria-[current=page]:bg-transparent aria-[current=page]:text-fg aria-[current=page]:hover:transform-none aria-[current=page]:hover:shadow-none";

/**
 * The column's body: the groups of a resource's sections under one raised piece, so it slides to the
 * section chosen even from one group to another. The current section is the one whose path is the longest
 * start of the page's route (`currentItem`), so a section beneath another is the only one marked.
 */
function ColumnList({ label, groups }: { label: string; groups: readonly ListGroup[] }) {
  const route = useRouterState({ select: (state) => state.matches.at(-1)?.fullPath ?? "" });
  const ids = useId();
  const all = useMemo(() => groups.flatMap((group) => group.items), [groups]);
  const current = currentItem(all, route);
  return (
    <nav aria-label={label} className="relative isolate flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-3 pb-4 scroll-thin">
      <SlidingIndicator className="rounded-control bg-surface" />
      {groups.map((group, position) => {
        const headingId = `${ids}-${String(position)}`;
        const previous = groups[position - 1];
        const newBlock = previous !== undefined && previous.scope !== group.scope && group.scope !== undefined;
        const titled = group.heading !== undefined || group.scope !== undefined;
        return (
          <section key={group.key} className={cx("flex flex-col gap-0.5", newBlock && "border-t border-border pt-4")}>
            {titled ? (
              <p id={headingId} className={cx("flex min-w-0 items-center gap-1.5 px-2.5 pb-1 text-13 font-semibold", group.danger === true ? "text-fail" : "text-fg-muted")}>
                {group.scope !== undefined ? <span className="min-w-0 truncate text-fg">{group.scope}</span> : null}
                {group.heading !== undefined ? <span className="min-w-0 truncate">{group.heading}</span> : null}
              </p>
            ) : null}
            <ul aria-labelledby={titled ? headingId : undefined} className="flex flex-col gap-0.5">
              {group.items.map((item) => (
                <li key={item.key}>
                  <Link
                    to={item.to}
                    params={item.params as never}
                    {...(item.search !== undefined ? { search: item.search as never } : {})}
                    activeOptions={{ exact: true, includeSearch: false }}
                    {...(current === item ? { "aria-current": "page" as const } : {})}
                    className={ITEM}
                  >
                    {item.icon !== undefined ? (
                      <item.icon aria-hidden="true" className="size-icon-md shrink-0 text-fg-faint group-hover:text-fg-muted group-aria-[current=page]:text-accent-fg" />
                    ) : null}
                    <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    {item.count !== undefined ? (
                      <span className={cx("mono min-w-5 rounded-chip bg-bg-sunken px-1 text-center text-12 text-fg-muted", item.count === null && "invisible")}>{item.count ?? 0}</span>
                    ) : null}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </nav>
  );
}

/** The column's head: the area the resource is under, as a way back to its list, and the resource's name. */
function ColumnHead({ area, to, name, server }: { area: string; to: string; name: ReactNode; server?: string | null }) {
  return (
    <div className="flex shrink-0 flex-col gap-1 px-4 pt-4 pb-3">
      <Link to={to} activeOptions={{ exact: true }} className="w-fit rounded-chip text-13 font-semibold text-fg-muted hover:text-fg">
        {area}
      </Link>
      <p translate="no" className="title min-w-0 truncate text-16 text-fg">
        {name}
      </p>
      {server !== undefined && server !== null ? (
        <p translate="no" className="flex min-w-0 items-center gap-1.5 text-13 text-fg-muted">
          <OBJECT_ICONS.server aria-hidden="true" className="size-icon-sm shrink-0" />
          <span className="min-w-0 truncate">{server}</span>
        </p>
      ) : null}
    </div>
  );
}

function toListGroups(groups: readonly ColumnGroup[], t: ReturnType<typeof useT>, params?: Record<string, string>, counts: Readonly<Partial<Record<string, number | null>>> = {}): ListGroup[] {
  return groups.map((group) => ({
    key: group.id,
    ...(group.titled && group.id !== "all" ? { heading: t(`nav.intents.${group.id}`) } : {}),
    ...(group.id === "danger" ? { danger: true } : {}),
    items: group.items.map((item) => ({
      key: item.id,
      to: item.to,
      ...(params !== undefined ? { params } : {}),
      label: t(item.label),
      icon: item.icon,
      ...(item.exact ? { exact: true } : {}),
      ...(counts[item.to] !== undefined ? { count: counts[item.to] } : {}),
    })),
  }));
}

function useCan(): (permission: string) => boolean {
  const { data: permissions } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });
  return (permission) => permissions?.includes(permission) ?? false;
}

/** An application's sections: use it, configure it, fix it, and what is dangerous. */
function AppColumn() {
  const t = useT();
  const { domain } = useParams({ strict: false });
  const can = useCan();
  const server = usePageServerName();
  const app = useQuery({ ...appQuery(domain ?? ""), enabled: domain !== undefined });
  if (domain === undefined) return null;
  const groups = toListGroups(appColumn({ can }, app.data !== undefined && isAdopted(app.data)), t, { domain });
  return (
    <>
      <ColumnHead area={t("nav.apps.label")} to="/apps" name={domain} server={server} />
      {app.data === undefined && !app.isError ? (
        <div aria-hidden="true" className="flex flex-col gap-1 px-3 pb-4">
          {Array.from({ length: 8 }, (_, row) => (
            <Skeleton key={row} className="h-8 w-full" />
          ))}
        </div>
      ) : (
        <ColumnList label={t("nav.landmarks.appSections")} groups={groups} />
      )}
    </>
  );
}

/** The machine's sections, with the counts its tabs have: the updates waiting and the findings open. */
function ServerColumn() {
  const t = useT();
  const can = useCan();
  const { node } = useNode();
  const hasFleet = useHasFleet();
  const { data: hostname } = useQuery({ ...sessionQuery(), select: (session) => session.hostname });
  const capability = useNodeCapability(SERVER_CAPABILITY);
  const available = capability.status === "available" || capability.status === "unknown";
  const summary = useQuery({ ...summaryQuery(), enabled: available });
  const security = useQuery({ ...securityOverviewQuery(), enabled: available });
  const found = security.data?.counts;
  const counts = available ? { "/server/system": summary.data?.updates.pending ?? null, "/server/security": found ? found.critical + found.warning : null } : {};
  return (
    <>
      <ColumnHead area={t("nav.server.label")} to={hasFleet ? "/fleet/servers" : "/server"} name={node ?? hostname ?? ""} />
      <ColumnList label={t("nav.landmarks.serverSections")} groups={toListGroups(serverColumn({ can, when: { fleet: node !== null } }), t, undefined, counts)} />
    </>
  );
}

/**
 * Settings: whose each section is, in the groups the register gives, and from twelve sections a box that
 * narrows them by name and by the name they had before (NV-11): twenty-four sections are searched, not read.
 */
function SettingsColumn() {
  const t = useT();
  const { items } = useSettingsItems();
  const [query, setQuery] = useState("");
  const filterable = items.length >= SETTINGS_FILTER_FROM;
  const narrowing = filterable && query.trim() !== "";
  const kept = narrowing ? items.filter((item) => answersFilter(item, query)) : items;
  const Search = ICONS.search;
  const groups: ListGroup[] = groupNavItems(kept).map((group) => ({
    key: group.key,
    ...(group.heading !== undefined ? { heading: group.heading } : {}),
    ...(group.scope !== undefined ? { scope: group.scope } : {}),
    items: group.items.map((item) => ({
      key: item.to,
      to: item.to,
      ...(item.search !== undefined ? { search: item.search } : {}),
      label: item.label,
      ...(item.icon !== undefined ? { icon: item.icon } : {}),
      ...(item.exact === true ? { exact: true } : {}),
    })),
  }));
  return (
    <>
      <div className="shrink-0 px-4 pt-4 pb-3">
        <p className="display min-w-0 truncate text-24 text-fg">{t("nav.settings.label")}</p>
      </div>
      {filterable ? (
        <div className="shrink-0 px-3 pb-3">
          <Input
            type="search"
            size="sm"
            aria-label={t("common.settingsLayout.filter")}
            placeholder={t("common.settingsLayout.filterPlaceholder")}
            // The page's `/` goes here when nothing else on it searches (the shell takes the first).
            data-page-search=""
            value={query}
            onValueChange={(value: string) => setQuery(value)}
            icon={<Search />}
            {...(query === "" ? { suffix: <Kbd className="pointer-coarse:hidden">/</Kbd> } : {})}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
      ) : null}
      {narrowing && kept.length === 0 ? (
        <p role="status" className="shrink-0 px-5 pb-3 text-13 text-fg-muted">
          {t("common.settingsLayout.noMatches")}
        </p>
      ) : null}
      <ColumnList label={t("nav.landmarks.settingsSections")} groups={groups} />
    </>
  );
}

/**
 * The second column of the double bar: the context of the resource the page is under (an application's
 * sections, the server's, Settings), beside the main bar and at the same height. It exists only where
 * the resource has sections, and Home has none: its fleet is a filter and each server leads to its page.
 * The column and the main bar each have their own raised piece.
 */
export function NavColumn({ kind }: { kind: ColumnKind }) {
  const t = useT();
  const { domain } = useParams({ strict: false });
  // Named by what it is about, so the two columns of the frame are told apart by a screen reader: the bar is
  // "Navigation" and this is the application, the machine or Settings.
  const name = kind === "app" ? (domain ?? t("nav.apps.label")) : kind === "server" ? t("nav.server.label") : t("nav.settings.label");
  return (
    <aside aria-label={name} data-frame="column" data-column={kind} className="sticky top-0 flex h-dvh w-column shrink-0 flex-col bg-bg-column">
      {kind === "app" ? <AppColumn /> : kind === "server" ? <ServerColumn /> : <SettingsColumn />}
    </aside>
  );
}
