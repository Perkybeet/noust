/**
 * The map of this server (checklist M-69): names, applications, databases, scheduled work and the
 * data an application keeps, in fixed columns with what connects them, read from the server on
 * every visit. Read only: it has no canvas to arrange and no coordinate to save, so the same server
 * is the same picture, and nothing moves when it is read again. Pointing at or focusing an element
 * lights its whole way, as in the site diagram; the same connections are a table, with a link to
 * each thing, and on a phone a list per column.
 */

import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useMemo } from "react";

import { mapQuery } from "../../api/queries/map";
import { overviewQuery } from "../../api/queries/overview";
import { LinkTabs } from "../../app/LinkTabs";
import { tabsOf } from "../../app/navCore";
import type { MapNode } from "../../api/queries/map";
import { CommandHint } from "../../components/page/CommandHint";
import { DashboardPage } from "../../components/page/DashboardPage";
import { QueryState } from "../../components/page/QueryState";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { FlowDiagram } from "../../components/ui/FlowDiagram";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { buttonClassName } from "../../components/ui/Button";
import { Link } from "@tanstack/react-router";
import { useT } from "../../i18n";
import { OVERVIEW_SCREENS } from "../nav/overview";
import { useTypeName } from "../apps/data";
import { countsOf, diagramOf } from "./model";

/** Where an element is opened, as a link in the table; the diagram itself only lights the way. */
function NodeLink({ node, name }: { node: MapNode | undefined; name: string }) {
  if (node === undefined) return <>{name}</>;
  if (node.kind === "database" && node.engine) {
    return (
      <TextLink to="/databases/$engine/$name" params={{ engine: node.engine, name: node.label }} size="ui">
        {name}
      </TextLink>
    );
  }
  if (node.kind === "scheduled") {
    return (
      <TextLink to="/cron" search={{ q: node.label }} size="ui">
        {name}
      </TextLink>
    );
  }
  if (node.domain) {
    return (
      <TextLink to="/apps/$domain" params={{ domain: node.domain }} size="ui">
        {name}
      </TextLink>
    );
  }
  return <>{name}</>;
}

function MapSkeleton() {
  // The frame the diagram will fill: a height is reserved from the first frame, so it does not jump.
  return (
    <div aria-hidden="true" className="flex flex-col gap-3">
      <Skeleton className="h-96 w-full rounded-card" />
      <Skeleton className="h-4 w-2/3" />
    </div>
  );
}

export function MapPage() {
  const t = useT();
  const query = useQuery(mapQuery());
  const overview = useQuery(overviewQuery());
  const server = overview.data?.server;
  const typeName = useTypeName();
  const diagram = useMemo(() => (query.data ? diagramOf(query.data, t, (type) => typeName(type) ?? type) : null), [query.data, t, typeName]);

  // An empty server replaces the diagram, as it replaces the Overview's figures: an empty diagram is
  // not a welcome.
  const empty = query.data?.layers.length === 0;

  return (
    // The Overview's second view (D-6): its header and its tabs, the map as the body of this one.
    <DashboardPage
      header={{
        title: t("overview.title"),
        description: t("overview.description"),
        // The line keeps its height before the answer arrives, so nothing below it moves.
        meta:
          server !== undefined ? (
            <>
              <Mono>{server.name}</Mono>
              <span>{t("overview.version", { version: server.version })}</span>
            </>
          ) : (
            <span aria-hidden="true" className="flex h-5 items-center">
              <Skeleton className="h-3 w-40" />
            </span>
          ),
        primaryAction: (
          <Link to="/apps/new" className={buttonClassName("primary")}>
            <Plus aria-hidden="true" />
            {t("overview.newApplication")}
          </Link>
        ),
      }}
      tabs={<LinkTabs label={t("nav.landmarks.overviewSections")} tabs={tabsOf(OVERVIEW_SCREENS)} />}
      footer={<CommandHint command="noust map" label={t("map.fromTerminal")} />}
    >
      {empty ? (
        <EmptyState variant="firstUse" title={t("map.emptyTitle")} description={t("map.emptyDescription")} command="noust create" />
      ) : (
        <QueryState query={query} label={t("map.queryLabel")} skeleton={<MapSkeleton />}>
          {(data) => {
            if (diagram === null) return null;
            const counts = countsOf(data);
            return (
              <div className="flex min-w-0 flex-col gap-4">
                {data.truncated ? <Notice tone="info">{t("map.truncated")}</Notice> : null}
                <FlowDiagram
                  layers={diagram.layers}
                  edges={diagram.edges}
                  label={t("map.label")}
                  animated={false}
                  legend={t("map.legend")}
                  summary={t("map.summary", counts)}
                  nodeLink={(node, name) => <NodeLink node={diagram.nodes.get(node.id)} name={typeof name === "string" ? name : node.label} />}
                />
              </div>
            );
          }}
        </QueryState>
      )}
    </DashboardPage>
  );
}
