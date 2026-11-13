import { useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { RowMenu } from "../../../components/page/RowMenu";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { MenuItem } from "../../../components/ui/Menu";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { TextLink } from "../../../components/ui/TextLink";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { useHasFleet, useServerList } from "../../../nodes/servers";
import { useNode } from "../../../nodes/useNode";
import { useFeature } from "../../central/features";
import { FaceMark } from "../fleet/FaceMark";
import type { Face } from "../fleet/faces";
import { ServersTable } from "../fleet/ServersTable";
import type { ServerLine } from "../fleet/ServersTable";
import { useOwnRows } from "./ownRows";
import type { OwnRow, OwnSection } from "./ownRows";

const FACE: Partial<Record<OwnRow["state"], Face>> = { off: "off", refused: "refused", unsupported: "unsupported", unknown: "unknown" };

/** The path each section has, so a row can lead to that server's own page of it. */
const PATH = {
  integrations: "/settings/integrations",
  "log-shipping": "/settings/log-shipping",
  "secret-managers": "/settings/secret-managers",
  "deploy-keys": "/settings/deploy-keys",
} as const;

/** What a server holds, in words: its names, a count of keys, or that it holds nothing. */
function valueOf(t: ReturnType<typeof useT>, section: OwnSection, row: OwnRow): ReactNode {
  if (row.state === "loading") return <Skeleton className="h-4 w-32" />;
  if (row.state !== "ok") return <span className="text-fg-faint">—</span>;
  if (section === "deploy-keys") return <span className={row.count === 0 ? "text-fg-muted" : undefined}>{row.count === 0 ? t("fleetSettings.own.none") : t("fleetSettings.own.countKeys", { count: row.count ?? 0 })}</span>;
  if (row.names.length === 0) return <span className="text-fg-muted">{t("fleetSettings.own.none")}</span>;
  return <span>{row.names.map((name) => (name === "github" ? t("fleetSettings.own.integrations.github") : name === "gitlab" ? t("fleetSettings.own.integrations.gitlab") : name)).join(", ")}</span>;
}

/**
 * The servers that are not this one, with what each holds of this section and the way to change it on that server.
 * Each is read through its own route and changed on its own page of the section: the central never writes it, because
 * it is the server's (a code host's keys, a secret manager's token, the place its logs go).
 */
function OtherServers({ section, label, valueHeader }: { section: OwnSection; label: string; valueHeader: string }) {
  const t = useT();
  const navigate = useNavigate();
  const { nodes } = useServerList();
  const rows = useOwnRows(section, nodes);
  if (nodes.length === 0) return null;
  const lines: ServerLine[] = rows.map((row) => {
    const face = FACE[row.state];
    const blocked = row.state !== "ok" && row.state !== "loading";
    return {
      name: row.node,
      value: valueOf(t, section, row),
      state: face !== undefined ? <FaceMark face={face} /> : <span className="text-13 text-fg-muted">{row.state === "loading" ? t("fleetSettings.own.reading") : t("fleetSettings.own.reachable")}</span>,
      menu: (
        <RowMenu name={row.node}>
          {blocked ? (
            <MenuItem disabled description={t("fleetSettings.own.manageOff")}>
              {t("fleetSettings.own.manage", { server: row.node })}
            </MenuItem>
          ) : (
            <MenuItem
              onClick={() => {
                void navigate({ to: PATH[section], search: { node: row.node } });
              }}
            >
              {t("fleetSettings.own.manage", { server: row.node })}
            </MenuItem>
          )}
        </RowMenu>
      ),
    };
  });
  return (
    <SurfaceSection title={t("fleetSettings.own.title")} icon={OBJECT_ICONS.node} description={t("fleetSettings.own.description")} flush>
      <ServersTable lines={lines} caption={t("fleetSettings.own.caption", { section: label })} valueHeader={valueHeader} />
    </SurfaceSection>
  );
}

/**
 * A section that is each server's own (code hosts, log shipping, secret managers, deploy keys), drawn as the section has always
 * been and, on a central, with a table of the other servers under it (item 166). With a server chosen the page is that
 * server's, and says where to go back to the table. A lone server sees it as it always was.
 */
export function OwnByServer({ section, label, valueHeader, children }: { section: OwnSection; label: string; valueHeader: string; children: ReactNode }) {
  const t = useT();
  const hasFleet = useHasFleet();
  const { node } = useNode();
  const deploysHere = useFeature("applications");
  if (!hasFleet) return <>{children}</>;
  if (node !== null) {
    return (
      <div className="flex min-w-0 flex-col gap-6">
        <TextLink to={PATH[section]} search={{ node: undefined }} size="ui" className="w-fit">
          {t("fleetSettings.own.back")}
        </TextLink>
        {children}
      </div>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {deploysHere ? children : null}
      <OtherServers section={section} label={label} valueHeader={valueHeader} />
    </div>
  );
}
