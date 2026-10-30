import { useQuery } from "@tanstack/react-query";
import { useRouterState, useSearch } from "@tanstack/react-router";

import { databasesQuery } from "../../api/queries/databases";
import { LogoMark } from "../../components/ui/LogoTile";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { engineGroups, logoFor } from "../../features/databases/servers";
import { useDatabaseServers } from "../../features/databases/servers/useDatabaseServers";
import { useT } from "../../i18n";
import { ColumnList } from "./ColumnList";
import type { ListGroup, ListItem } from "./ColumnList";

/**
 * Databases: its two sections (the databases and the servers they run on) and, under "Engines", one view
 * per engine there is a server of, with the databases each holds. An engine keeps the section the person is
 * in, so the same choice narrows the databases or the servers; the one chosen is the current item, and with
 * none chosen it is the section.
 */
export default function DatabasesColumn() {
  const t = useT();
  const databases = useQuery(databasesQuery());
  const { servers } = useDatabaseServers();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const engine = useSearch({ strict: false, select: (search: { engine?: string }) => search.engine });
  const onServers = pathname.startsWith("/databases/servers") || pathname.startsWith("/databases/engines");
  const section = onServers ? "/databases/servers" : "/databases";
  const groups = engineGroups(servers ?? [], databases.data?.databases ?? []);
  const known = groups.some((group) => group.flavour === engine);
  const list: ListGroup[] = [
    {
      key: "sections",
      items: [
        { key: "databases", to: "/databases", label: t("dbServers.column.databases"), icon: OBJECT_ICONS.database, exact: true, count: databases.data === undefined ? null : databases.data.total },
        { key: "servers", to: "/databases/servers", label: t("dbServers.column.servers"), icon: OBJECT_ICONS.server, exact: true, count: servers === undefined ? null : servers.length },
      ],
    },
    ...(groups.length > 0
      ? [
          {
            key: "engines",
            heading: t("dbServers.column.engines"),
            items: groups.map((group): ListItem => {
              const logo = logoFor(group.flavour);
              return {
                key: `engine:${group.flavour}`,
                to: section,
                search: { engine: group.flavour },
                label: group.name,
                ...(logo !== null ? { mark: <LogoMark logo={logo} className="size-icon-md" /> } : { icon: OBJECT_ICONS.database }),
                count: group.databases,
              };
            }),
          },
        ]
      : []),
  ];
  return (
    <>
      <div className="shrink-0 px-4 pt-4 pb-3">
        <p className="display min-w-0 truncate text-24 text-fg">{t("dbServers.column.label")}</p>
      </div>
      <ColumnList label={t("dbServers.column.label")} groups={list} currentKey={known ? `engine:${engine}` : onServers ? "servers" : "databases"} />
    </>
  );
}
