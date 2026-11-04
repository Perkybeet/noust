import { useQuery } from "@tanstack/react-query";
import { useNavigate, useRouterState } from "@tanstack/react-router";

import { appQuery, appsQuery } from "../../api/queries/apps";
import { deploymentsQuery } from "../../api/queries/deployments";
import { databaseOverviewQuery, databasesQuery, enginesQuery } from "../../api/queries/databases";
import { serviceQuery, servicesQuery } from "../../api/queries/services";
import { appStatus } from "../../components/page/status";
import { StatusGlyph, StatusPill } from "../../components/ui/StatusPill";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { isAdopted } from "../../features/app/adopted";
import { RECENT_APP_DEPLOYS } from "../../features/app/AppBanner";
import { usePendingChanges } from "../../features/app/PendingChanges";
import { AppTypeMark } from "../../features/apps/AppTypeMark";
import { ASSUMED_CAPABILITIES } from "../../features/databases/tabs";
import { SERVER_CAPABILITY } from "../../features/server/queries";
import { serviceState } from "../../features/services/data";
import { useOrganization } from "../../features/teams/useOrganization";
import { useT } from "../../i18n";
import { useNodeCapability } from "../../nodes/capability";
import { NODE_STATE, nodeStatus, useServerList } from "../../nodes/servers";
import { useNode, useSwitchNode } from "../../nodes/useNode";
import { appColumn, areaColumn, resourceColumn } from "../columns";
import { ColumnList } from "./ColumnList";
import { ResourceHead } from "./ColumnHead";
import type { Sibling } from "./ColumnHead";
import { SectionsSkeleton, toListGroups, useAreaCounts, useCan, useNavContext } from "./columnBodies";
import { usePageServerName } from "./usePageServerName";

/** How many other resources the switch lists: it is a way to jump, not the list (the area is). */
const SIBLINGS = 12;

/** The jump to a sibling keeps to the same section where that means something on the other one. */
function sameSection(route: string, params: Record<string, string>, own: readonly string[], fallback: string): { to: string; params: Record<string, string> } {
  const used = [...route.matchAll(/\$(\w+)/g)].map((match) => match[1] ?? "");
  const only = used.every((name) => own.includes(name));
  return only ? { to: route.replace(/\/+$/, ""), params } : { to: fallback, params };
}

/** An application's sections: use it, configure it, fix it, take it elsewhere, and what is dangerous. */
export function AppColumn({ domain }: { domain: string }) {
  const t = useT();
  const navigate = useNavigate();
  const can = useCan();
  const server = usePageServerName();
  const route = useRouterState({ select: (state) => state.matches.at(-1)?.fullPath ?? "" });
  const app = useQuery(appQuery(domain));
  const apps = useQuery({ ...appsQuery(), select: (data) => data.apps });
  // What its sections count, as its tabs did: the deployments building or waiting, and the environment's settings not applied yet.
  const deploys = useQuery(deploymentsQuery({ domain, limit: RECENT_APP_DEPLOYS }));
  const pending = usePendingChanges(domain);
  const building = (deploys.data?.items ?? []).filter((item) => item.status === "running" || item.status === "queued").length;
  const unapplied = (pending.data?.items ?? []).filter((item) => item.area === "environment").length;
  const counts = { ...(building > 0 ? { "/apps/$domain/deployments": building } : {}), ...(unapplied > 0 ? { "/apps/$domain/environment": unapplied } : {}) };
  const groups = toListGroups(appColumn({ can }, app.data !== undefined && isAdopted(app.data)), t, { domain }, counts);
  const state = app.data === undefined ? undefined : appStatus(app.data.status, t.locale);
  const siblings: Sibling[] = (apps.data ?? []).slice(0, SIBLINGS + 1).map((other) => {
    const view = appStatus(other.status, t.locale);
    return {
      key: other.domain,
      label: other.domain,
      current: other.domain === domain,
      glyph: <StatusGlyph state={view.state} />,
      onChoose: () => {
        const target = sameSection(route, { domain: other.domain }, ["domain"], "/apps/$domain");
        void navigate({ to: target.to, params: target.params as never });
      },
    };
  });
  return (
    <>
      <ResourceHead
        back={{ label: t("nav.apps.label"), to: "/apps" }}
        title={domain}
        server={server}
        {...(app.data?.app_type !== undefined ? { mark: <AppTypeMark type={app.data.app_type} size="sm" /> } : { icon: OBJECT_ICONS.app })}
        {...(state !== undefined ? { state: <StatusPill state={state.state} label={state.label} size="sm" /> } : {})}
        siblings={siblings}
        siblingsLabel={t("nav.column.switchApp")}
      />
      {/* An application that cannot be read (there is none at that name) has no sections to list: the page says why. */}
      {app.isError ? null : app.data === undefined ? <SectionsSkeleton label={t("nav.landmarks.appSections")} /> : <ColumnList label={t("nav.landmarks.appSections")} groups={groups} />}
    </>
  );
}

/** A database's sections, from what its engine can do: Data or Keys, Query where there is a console, and the rest. */
export function DatabaseColumn({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const navigate = useNavigate();
  const route = useRouterState({ select: (state) => state.matches.at(-1)?.fullPath ?? "" });
  const overview = useQuery(databaseOverviewQuery(engine, name));
  const engines = useQuery(enginesQuery());
  const databases = useQuery({ ...databasesQuery(), select: (data) => data.databases });
  const listed = engines.data?.engines.find((item) => item.name === engine);
  const capabilities = overview.data?.capabilities ?? listed?.capabilities;
  const ctx = useNavContext({});
  // Until the engine answers, what a SQL engine has is drawn: nothing then moves when it does.
  const groups = toListGroups(resourceColumn("database", { ...ctx, capabilities: capabilities ?? ASSUMED_CAPABILITIES }), t, { engine, name });
  const missing = overview.data?.database.missing === true;
  const siblings: Sibling[] = (databases.data ?? []).slice(0, SIBLINGS + 1).map((other) => ({
    key: `${other.engine}/${other.name}`,
    label: other.name,
    current: other.engine === engine && other.name === name,
    onChoose: () => {
      const target = sameSection(route, { engine: other.engine, name: other.name }, ["engine", "name"], "/databases/$engine/$name");
      void navigate({ to: target.to, params: target.params as never });
    },
  }));
  return (
    <>
      <ResourceHead
        back={{ label: t("nav.databases.label"), to: "/databases" }}
        title={name}
        icon={OBJECT_ICONS.database}
        {...(overview.data !== undefined
          ? { state: <StatusPill state={missing ? "failed" : "running"} label={t(missing ? "databases.layout.stateMissing" : "databases.layout.stateOnline")} size="sm" />, kind: overview.data.display_name }
          : {})}
        siblings={siblings}
        siblingsLabel={t("nav.column.switchDatabase")}
      />
      <ColumnList label={t("nav.landmarks.databaseSections")} groups={groups} />
    </>
  );
}

/** A service of the machine: its overview and its logs. */
export function ServiceColumn({ name }: { name: string }) {
  const t = useT();
  const navigate = useNavigate();
  const route = useRouterState({ select: (state) => state.matches.at(-1)?.fullPath ?? "" });
  const ctx = useNavContext();
  const service = useQuery(serviceQuery(name));
  const services = useQuery({ ...servicesQuery(), select: (data) => data.services });
  const view = service.data === undefined ? undefined : serviceState(service.data);
  const siblings: Sibling[] = (services.data ?? []).slice(0, SIBLINGS + 1).map((other) => ({
    key: other.name,
    label: other.name,
    current: other.name === name,
    onChoose: () => {
      const target = sameSection(route, { name: other.name }, ["name"], "/server/services/$name");
      void navigate({ to: target.to, params: target.params as never });
    },
  }));
  return (
    <>
      <ResourceHead
        back={{ label: t("server.tabs.services"), to: "/server/services" }}
        title={name}
        icon={OBJECT_ICONS.service}
        {...(view !== undefined ? { state: <StatusPill state={view.state} label={t(view.label)} size="sm" /> } : {})}
        siblings={siblings}
        siblingsLabel={t("nav.column.switchService")}
      />
      <ColumnList label={t("nav.landmarks.serviceSections")} groups={toListGroups(resourceColumn("service", ctx), t, { name })} />
    </>
  );
}

/** A team's sections: who is in it, what it shares, and its settings. */
export function TeamColumn({ team }: { team: string }) {
  const t = useT();
  const ctx = useNavContext();
  const org = useOrganization();
  const name = org.teams.find((candidate) => candidate.slug === team)?.name ?? team;
  return (
    <>
      <ResourceHead back={{ label: t("teams.list.title"), to: "/projects/teams" }} title={name} icon={OBJECT_ICONS.team} />
      <ColumnList label={t("nav.landmarks.teamSections")} groups={toListGroups(resourceColumn("team", ctx), t, { team })} />
    </>
  );
}


/** The machine on a fleet: it is a resource of the list of servers, with the way back to it and the switch to the others. */
export function ServerResourceColumn() {
  const t = useT();
  const { node } = useNode();
  const switchNode = useSwitchNode();
  const servers = useServerList();
  const ctx = useNavContext({ fleet: node !== null });
  const capability = useNodeCapability(SERVER_CAPABILITY);
  const available = capability.status === "available" || capability.status === "unknown";
  const counts = useAreaCounts("server", available);
  const name = node ?? servers.hostname ?? "";
  const current = node === null ? undefined : servers.nodes.find((candidate) => candidate.name === node);
  const view = current === undefined ? undefined : NODE_STATE[nodeStatus(current)];
  const everyone: { name: string | null; label: string; state?: (typeof NODE_STATE)[keyof typeof NODE_STATE] }[] = [
    ...(servers.hostname === null ? [] : [{ name: null, label: servers.hostname }]),
    ...servers.nodes.map((candidate) => ({ name: candidate.name, label: candidate.name, state: NODE_STATE[nodeStatus(candidate)] })),
  ];
  const siblings: Sibling[] = everyone.map((other) => ({
    key: other.name ?? "this",
    label: other.label,
    current: other.name === node,
    ...(other.state !== undefined ? { glyph: <StatusGlyph state={other.state.state} /> } : {}),
    onChoose: () => void switchNode(other.name),
  }));
  return (
    <>
      <ResourceHead
        back={{ label: t("nav.server.allServers"), to: "/fleet/servers" }}
        title={name}
        icon={OBJECT_ICONS.server}
        {...(view !== undefined ? { state: <StatusPill state={view.state} label={t(view.label)} size="sm" /> } : {})}
        siblings={siblings}
        siblingsLabel={t("nav.column.switchServer")}
      />
      <ColumnList label={t("nav.landmarks.serverSections")} groups={toListGroups(areaColumn("server", ctx), t, undefined, counts)} />
    </>
  );
}
