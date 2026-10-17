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
import type { MapNode } from "../../api/queries/map";
import { CommandHint } from "../../components/page/CommandHint";
import { DashboardPage } from "../../components/page/DashboardPage";
import { QueryState } from "../../components/page/QueryState";
import { EmptyState } from "../../components/ui/EmptyState";
import { FlowDiagram } from "../../components/ui/FlowDiagram";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { buttonClassName } from "../../components/ui/Button";
import { Link } from "@tanstack/react-router";
import { useT } from "../../i18n";
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
  const typeName = useTypeName();
  const diagram = useMemo(() => (query.data ? diagramOf(query.data, t, (type) => typeName(type) ?? type) : null), [query.data, t, typeName]);

  // An empty server replaces everything below the header, as on the Overview: an empty diagram is
  // not a welcome.
  const empty = query.data?.layers.length === 0;

  return (
    <DashboardPage
      header={{
        title: t("map.title"),
        description: t("map.description"),
        primaryAction: (
          <Link to="/apps/new" className={buttonClassName("primary")}>
            <Plus aria-hidden="true" />
            {t("map.newApplication")}
          </Link>
        ),
      }}
      firstSteps={
        empty ? <EmptyState variant="firstUse" title={t("map.emptyTitle")} description={t("map.emptyDescription")} command="noust create" /> : undefined
      }
      charts={
        <div className="col-span-full min-w-0">
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
        </div>
      }
      footer={<CommandHint command="noust map" label={t("map.fromTerminal")} />}
    />
  );
}
