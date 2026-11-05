import { useQuery } from "@tanstack/react-query";

import { appsQuery } from "../../api/queries/apps";
import { sessionQuery } from "../../api/queries/auth";
import { backupDestinationsQuery } from "../../api/queries/backupDestinations";
import { backupSchedulesQuery, backupsQuery } from "../../api/queries/backups";
import { certsQuery } from "../../api/queries/certs";
import { databasesQuery, serversQuery } from "../../api/queries/databases";
import { dnsAccountsQuery, streamsQuery, tunnelsQuery } from "../../api/queries/network";
import { installedRecipesQuery, recipesQuery } from "../../api/queries/recipes";
import { sitesQuery } from "../../api/queries/sites";
import { volumePoliciesQuery } from "../../api/queries/volumeBackups";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { Skeleton } from "../../components/ui/Skeleton";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { domainRows } from "../../features/domains/domainRows";
import { SERVER_CAPABILITY, securityOverviewQuery, summaryQuery } from "../../features/server/queries";
import { countByOwner } from "../../features/teams/organization";
import { useOrganised } from "../../features/teams/useOrganised";
import { useOrganization } from "../../features/teams/useOrganization";
import { useT } from "../../i18n";
import { useNodeCapability } from "../../nodes/capability";
import { useHasFleet, useServerList } from "../../nodes/servers";
import { useConsoleContext, useNode } from "../../nodes/useNode";
import { accountColumn, areaColumn } from "../columns";
import type { ColumnGroup, ColumnKind } from "../columns";
import { dest } from "../nav";
import type { NavContext } from "../navCore";
import { ColumnHead } from "./ColumnHead";
import { ColumnList } from "./ColumnList";
import type { ListGroup } from "./ColumnList";

export function useCan(): (permission: string) => boolean {
  const { data: permissions } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });
  return (permission) => permissions?.includes(permission) ?? false;
}

/** What the register's lists are drawn for: who is looking and which optional parts apply. */
export function useNavContext(extra: NavContext["when"] = {}): NavContext {
  const can = useCan();
  const { organised } = useOrganised();
  const hasFleet = useHasFleet();
  return { can, when: { organised, fleet: hasFleet, ...extra } };
}

/** The register's groups as the column draws them: titled by the catalog, one link per section. */
export function toListGroups(groups: readonly ColumnGroup[], t: ReturnType<typeof useT>, params?: Record<string, string>, counts: Readonly<Partial<Record<string, number | null>>> = {}): ListGroup[] {
  return groups.map((group) => ({
    key: group.id === "" ? "all" : group.id,
    ...(group.heading !== undefined ? { heading: t(group.heading) } : {}),
    ...(group.danger === true ? { danger: true } : {}),
    items: group.items.map((item) => ({
      key: item.id,
      to: item.to,
      ...(params !== undefined ? { params } : {}),
      ...(item.search !== undefined ? { search: { ...item.search } } : {}),
      label: t(item.label),
      icon: item.icon,
      ...(item.exact ? { exact: true } : {}),
      ...((counts[item.id] ?? counts[item.to]) !== undefined ? { count: counts[item.id] ?? counts[item.to] } : {}),
    })),
  }));
}

/** What a body of the column stands for, to the screen reader: the sections of the area or the resource. */
export function columnLabel(kind: ColumnKind, t: ReturnType<typeof useT>, domain?: string): string {
  switch (kind) {
    case "app":
      return domain ?? t("nav.apps.label");
    case "settings":
      return t("nav.landmarks.settingsSections");
    case "server":
      return t("nav.landmarks.serverSections");
    case "databases":
      return t("nav.databases.label");
    case "database":
      return t("nav.landmarks.databaseSections");
    case "service":
      return t("nav.landmarks.serviceSections");
    case "team":
      return t("nav.landmarks.teamSections");
    case "account":
      return t("nav.landmarks.accountSections");
    default:
      return t("nav.landmarks.areaSections", { area: t(dest(kind).label) });
  }
}


/**
 * The room of a list of sections while what it reads arrives: the list is as tall as it will be, so nothing moves.
 * A busy region that says what it is reading, so a screen reader hears it and not a shape.
 */
export function SectionsSkeleton({ label }: { label: string }) {
  const t = useT();
  return (
    <div className="px-3 pb-4">
      <LoadingRegion label={t("common.queryState.loading", { label })} className="flex flex-col gap-1">
        {Array.from({ length: 8 }, (_, row) => (
          <Skeleton key={row} className="h-8 w-full" />
        ))}
      </LoadingRegion>
    </div>
  );
}

/** The person's own account: their profile and the security of their sign-in. */
export function AccountColumn() {
  const t = useT();
  const ctx = useNavContext();
  return (
    <>
      <ColumnHead title={t("account.title")} />
      <ColumnList label={t("nav.landmarks.accountSections")} groups={toListGroups(accountColumn(ctx), t)} />
    </>
  );
}

/**
 * What the sections of an area count, as the tabs of 4.1 did: the updates waiting and the findings open, the
 * backups, the names and certificates, the tunnels and the published ports. A count is `null` while it loads,
 * and keeps its room, so the column does not change when it arrives. Only the area on screen reads its own.
 */
export function useAreaCounts(kind: ColumnKind, enabled: boolean): Readonly<Partial<Record<string, number | null>>> {
  const on = (area: ColumnKind): boolean => enabled && kind === area;
  const summary = useQuery({ ...summaryQuery(), enabled: on("server") });
  const security = useQuery({ ...securityOverviewQuery(), enabled: on("security") });
  const backups = useQuery({ ...backupsQuery(null), enabled: on("backups") });
  const schedules = useQuery({ ...backupSchedulesQuery(), enabled: on("backups") });
  const volumes = useQuery({ ...volumePoliciesQuery(), enabled: on("backups") });
  const destinations = useQuery({ ...backupDestinationsQuery(), enabled: on("backups") });
  const recipes = useQuery({ ...recipesQuery(), enabled: on("catalog") });
  const installed = useQuery({ ...installedRecipesQuery(), enabled: on("catalog") });
  const certs = useQuery({ ...certsQuery(), enabled: on("domains") });
  const sites = useQuery({ ...sitesQuery(), enabled: on("domains") });
  const accounts = useQuery({ ...dnsAccountsQuery(), enabled: on("domains") });
  const tunnels = useQuery({ ...tunnelsQuery(), enabled: on("network") });
  const streams = useQuery({ ...streamsQuery(), enabled: on("network") });
  const databases = useQuery({ ...databasesQuery(), enabled: on("databases") });
  const applications = useQuery({ ...appsQuery(), enabled: on("apps") });
  const dbServers = useQuery({ ...serversQuery(), enabled: on("databases") });
  const org = useOrganization();
  if (!enabled) return {};
  const found = security.data?.counts;
  switch (kind) {
    case "server":
      return { "/server/updates": summary.data?.updates.pending ?? null };
    case "security":
      return { "/security": found ? found.critical + found.warning : null };
    case "backups":
      return {
        "/backups": backups.data?.total ?? null,
        "/backups/schedules": schedules.data?.total ?? null,
        "/backups/volumes": volumes.data?.total ?? null,
        "/backups/destinations": destinations.data?.total ?? null,
      };
    case "catalog":
      return { "/catalog": recipes.data?.total ?? null, "/catalog/installed": installed.data?.items.length ?? null };
    case "domains":
      return {
        "/domains": sites.data === undefined || certs.data === undefined ? null : domainRows(sites.data.sites, certs.data.certificates).length,
        "/domains/sites": sites.data?.total ?? null,
        "/domains/certificates": certs.data?.total ?? null,
        "/domains/dns": accounts.data?.accounts.length ?? null,
      };
    case "databases":
      return { "/databases": databases.data?.total ?? null, "/databases/servers": dbServers.data?.servers.length ?? null };
    case "apps": {
      // Each view of the list says nothing of how many; "All" says how many there are, and each project how many it holds.
      const held = countByOwner(applications.data?.apps ?? []);
      return {
        "apps.view.all": applications.data?.apps.length ?? null,
        "/projects": org.available ? org.scope.projects.length : null,
        "/projects/teams": org.available ? org.teams.length : null,
        ...Object.fromEntries((org.available ? org.scope.projects : []).map((project) => [`project:${project.team}/${project.slug}`, applications.data === undefined ? null : (held.get(`${project.team}/${project.slug}`) ?? 0)])),
      };
    }
    case "network":
      return { "/network": streams.data?.exposures.length ?? null, "/network/tunnels": tunnels.data?.tunnels.length ?? null };
    default:
      return {};
  }
}

/** The servers of the fleet as links that choose one: what "All servers" lists under its faces. */
function nodeGroup(t: ReturnType<typeof useT>, nodes: readonly { name: string }[], to: string): ListGroup {
  return {
    key: "servers",
    heading: t("nav.columnGroups.servers"),
    items: nodes.map((node) => ({ key: `node:${node.name}`, to, search: { node: node.name }, label: node.name, icon: OBJECT_ICONS.server })),
  };
}

/**
 * The column of an area that has pages of its own or fixed views: its sections as the register gives
 * them, and from "All servers" the fleet's faces of it. The machine's areas (Network, Security, Server)
 * list the servers there, since choosing one is choosing it in the selector.
 */
export function AreaColumn({ kind }: { kind: ColumnKind }) {
  const t = useT();
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  const { node } = useNode();
  const servers = useServerList();
  const fleet = context.kind === "fleet";
  const ctx = useNavContext({ fleet: kind === "server" ? node !== null : hasFleet });
  const capability = useNodeCapability(SERVER_CAPABILITY);
  const available = capability.status === "available" || capability.status === "unknown";
  const counts = useAreaCounts(kind, available && !fleet);
  const org = useOrganization();
  const { organised } = useOrganised();
  const area = dest(kind);
  const groups = toListGroups(areaColumn(kind, ctx, fleet ? "fleet" : "server"), t, undefined, counts);
  if (fleet && (kind === "server" || kind === "network" || kind === "security")) groups.push(nodeGroup(t, servers.nodes, "/server"));
  // Under the views of the applications, the projects they are grouped in: a way to one project without opening the list, and
  // the pages that manage them (all the projects, and the teams) as the last of the group instead of two more views.
  if (kind === "apps" && !fleet) {
    const managing = new Set(["apps.projects", "apps.projects.teams"]);
    const moved = groups.flatMap((group) => group.items.filter((item) => managing.has(item.key)));
    for (const group of groups) group.items = group.items.filter((item) => !managing.has(item.key));
    if (org.available && organised && org.scope.projects.length > 0) {
      groups.push({
        key: "projects",
        heading: t("nav.columnGroups.projects"),
        items: [
          ...org.scope.projects.map((project) => ({
            key: `project:${project.team}/${project.slug}`,
            to: "/projects/$team/$project",
            params: { team: project.team, project: project.slug },
            label: project.name,
            icon: OBJECT_ICONS.project,
            count: counts[`project:${project.team}/${project.slug}`] ?? null,
          })),
          ...moved,
        ],
      });
    }
  }
  const title = kind === "server" ? t(fleet ? "nav.server.label" : "nav.server.labelOne") : t(area.label);
  return (
    <>
      <ColumnHead title={title} />
      <ColumnList label={columnLabel(kind, t)} groups={groups} />
    </>
  );
}

