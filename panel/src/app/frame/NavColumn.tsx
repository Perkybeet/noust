import { useParams } from "@tanstack/react-router";
import { Suspense, lazy } from "react";

import { useHomeAddress } from "../../features/overview/search";
import { useT } from "../../i18n";
import { useHasFleet, useServerList } from "../../nodes/servers";
import { useConsoleContext, useNode } from "../../nodes/useNode";
import { depthOf } from "../columns";
import type { ColumnKind } from "../columns";
import { AccountColumn, AreaColumn, SectionsSkeleton, columnLabel } from "./columnBodies";
import { ColumnHead } from "./ColumnHead";
import { ColumnLayers } from "./ColumnLayers";
import { SettingsColumn } from "./SettingsColumn";

// The databases' column draws the engines with their logos and reads the servers: it is a chunk of its own, so the
// script every page loads does not carry what only /databases draws.
const DatabasesColumn = lazy(() => import("./DatabasesColumn"));

// Home's column carries the gallery and with it every widget: a chunk of its own, drawn with Home and never by the script every page loads.
const HomeColumn = lazy(() => import("../../features/overview/HomeColumn").then((module) => ({ default: module.HomeColumn })));

// The sections of an application, a database, a service, a team and a server read the details of each: one chunk, drawn
// only where one is entered.
const AppColumn = lazy(() => import("./resourceColumns").then((module) => ({ default: module.AppColumn })));
const DatabaseColumn = lazy(() => import("./resourceColumns").then((module) => ({ default: module.DatabaseColumn })));
const ServiceColumn = lazy(() => import("./resourceColumns").then((module) => ({ default: module.ServiceColumn })));
const TeamColumn = lazy(() => import("./resourceColumns").then((module) => ({ default: module.TeamColumn })));
const ServerResourceColumn = lazy(() => import("./resourceColumns").then((module) => ({ default: module.ServerResourceColumn })));

type Params = Readonly<Record<string, string | undefined>>;

/** What a body of the column needs to know besides the area: the resource in the address, and where the person is. */
interface Where {
  kind: ColumnKind;
  params: Params;
  customising: boolean;
  /** The page is one of the fleet's ("All servers"). */
  fleet: boolean;
  hasFleet: boolean;
}

/** The body of one list of the column. A self-contained element, so the one that is leaving keeps working while it goes. */
function ColumnBody(where: Where) {
  const t = useT();
  return (
    <Suspense fallback={<SectionsSkeleton label={columnLabel(where.kind, t, where.params["domain"])} />}>
      <ColumnBodyOf {...where} />
    </Suspense>
  );
}

function ColumnBodyOf({ kind, params, customising, fleet, hasFleet }: Where) {
  const t = useT();
  switch (kind) {
    case "overview":
      return <HomeColumn customising={customising} />;
    case "app":
      return params["domain"] === undefined ? null : <AppColumn domain={params["domain"]} />;
    case "database":
      return params["engine"] === undefined || params["name"] === undefined ? null : <DatabaseColumn engine={params["engine"]} name={params["name"]} />;
    case "service":
      return params["name"] === undefined ? null : <ServiceColumn name={params["name"]} />;
    case "team":
      return params["team"] === undefined ? null : <TeamColumn team={params["team"]} />;
    case "settings":
      return <SettingsColumn />;
    case "account":
      return <AccountColumn />;
    case "databases":
      return (
        <Suspense fallback={<ColumnHead title={t("dbServers.column.label")} />}>
          <DatabasesColumn />
        </Suspense>
      );
    case "server":
      // On a fleet the machine is a resource of the list of servers; on a lone server it is the area itself.
      return hasFleet && !fleet ? <ServerResourceColumn /> : <AreaColumn kind="server" />;
    default:
      return <AreaColumn kind={kind} />;
  }
}

/**
 * What names the list on screen, so a new one is a slide: an area's is its kind (and the fleet's face of
 * it), a resource's is its kind and its name, the machine's on a fleet is the server's.
 */
function layerKeyOf({ kind, params, customising, fleet, hasFleet }: Where, node: string | null): string {
  if (kind === "overview") return customising ? "overview:customise" : "overview";
  if (kind === "app") return `app:${params["domain"] ?? ""}`;
  if (kind === "database") return `database:${params["engine"] ?? ""}/${params["name"] ?? ""}`;
  if (kind === "service") return `service:${params["name"] ?? ""}`;
  if (kind === "team") return `team:${params["team"] ?? ""}`;
  if (kind === "server" && hasFleet && !fleet) return `server:${node ?? "this"}`;
  return fleet ? `${kind}:fleet` : kind;
}

/** How deep the list is: the machine on a fleet is a level under the list of servers, and Home's gallery under its panels. */
function depthOfList({ kind, customising, fleet, hasFleet }: Where): number {
  if (kind === "server") return hasFleet && !fleet ? 1 : 0;
  if (kind === "overview") return customising ? 1 : 0;
  return depthOf(kind);
}

function useWhere(kind: ColumnKind): Where {
  const params = useParams({ strict: false });
  const context = useConsoleContext();
  const { node } = useNode();
  // A page of a server of the list is a fleet's whatever the list says yet: the address is the proof.
  const hasFleet = useHasFleet() || node !== null;
  const { customising } = useHomeAddress();
  return { kind, params, customising, fleet: context.kind === "fleet", hasFleet };
}

/**
 * The second column of the frame: the sections of the area the page is under, or of the resource it
 * enters, beside the main bar and at the same height. Every area has one (NV-22). Where a person enters a
 * resource the list slides to its sections, with the way back to the area, and slides back out
 * (ColumnLayers); Home's is drawn by Home (`features/overview/HomeColumn`: its panels), and while Home is
 * being customised (`?customise` in its address) it is a level deeper, as the gallery of widgets. The column
 * and the main bar each have their own raised piece.
 */
export function NavColumn({ kind }: { kind: ColumnKind }) {
  const t = useT();
  const where = useWhere(kind);
  const { node } = useNode();
  const servers = useServerList();
  // The machine's column is one of two lists, by whether there is a fleet: until the servers are known the first is a guess.
  const settled = kind !== "server" || where.hasFleet || servers.loaded;
  // Named by what it is about, so the two columns of the frame are told apart by a screen reader: the bar is
  // "Navigation" and this is the application, the machine or Settings.
  const name = kind === "overview" ? t("nav.overview.label") : columnLabel(kind, t, where.params["domain"]);
  return (
    <aside aria-label={name} data-frame="column" data-column={kind} className="sticky top-0 flex h-dvh w-column shrink-0 flex-col bg-bg-column">
      <ColumnLayers layerKey={layerKeyOf(where, node)} depth={depthOfList(where)} settled={settled}>
        <ColumnBody {...where} />
      </ColumnLayers>
    </aside>
  );
}

/** The body of the column on its own, for the phone's sheet: the same list the column draws at the side. */
export function ColumnContent({ kind }: { kind: ColumnKind }) {
  return <ColumnBody {...useWhere(kind)} />;
}
