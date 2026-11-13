/**
 * Server > Docker > Engine: which Docker there is, what fits in it and what would not come back after a restart. The engine's own
 * doctor says it in sentences (what it found, and what does not fit and why), each with its fix; under them, the engine's facts, one per
 * row, for whoever has to tell another person which Docker this is. It is also where a machine with no usable Docker learns why.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { dockerEngineQuery, dockerRestartLossQuery } from "../../api/queries/docker";
import type { DockerEngine, DockerRestartLoss } from "../../api/queries/docker";
import { DetailPage } from "../../components/page/DetailPage";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { QueryState } from "../../components/page/QueryState";
import { Sections } from "../../components/page/Section";
import { Subsection } from "../../components/page/Subsection";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { NodeCapabilityGate } from "../../nodes/capability";
import { EngineSentences } from "./Unavailable";
import { ContainerLink, OwnerCell } from "./pieces";
import { DOCKER_CAPABILITY } from "./SummaryPage";

type Lost = NonNullable<DockerRestartLoss["items"]>[number];

/** The engine's facts, one per row: what is empty is left out, so a Docker that does not say is not drawn as a row of nothing. */
export function engineFacts(info: DockerEngine["info"], t: ReturnType<typeof useT>): KeyValueItem[] {
  const rows: (KeyValueItem | null)[] = [
    info.server_version !== "" ? { label: t("docker.engine.version"), value: `Docker Engine ${info.server_version}`, mono: true } : null,
    info.server_api !== "" ? { label: t("docker.engine.api"), value: info.server_api, ...(info.min_api !== "" ? { hint: t("docker.engine.apiFrom", { version: info.min_api }) } : {}), mono: true } : null,
    info.compose_version !== "" ? { label: t("docker.engine.compose"), value: info.compose_version, mono: true } : null,
    info.storage_driver !== "" ? { label: t("docker.engine.storage"), value: info.storage_driver, mono: true } : null,
    info.image_store !== "" ? { label: t("docker.engine.imageStore"), value: info.image_store, mono: true } : null,
    info.root_dir !== "" ? { label: t("docker.engine.rootDir"), value: info.root_dir, mono: true } : null,
    info.socket !== "" ? { label: t("docker.engine.socket"), value: info.socket, mono: true } : null,
    { label: t("docker.engine.rootless"), value: info.rootless ? t("docker.engine.rootlessYes") : t("docker.engine.rootlessNo"), mono: false, copy: false },
  ];
  return rows.filter((row): row is KeyValueItem => row !== null);
}

function RestartLoss({ items }: { items: readonly Lost[] }) {
  const t = useT();
  const columns: Column<Lost>[] = [
    { id: "name", header: t("docker.columns.container"), card: "title", cell: (row) => <ContainerLink name={row.name} /> },
    { id: "owner", header: t("docker.columns.owner"), card: "meta", cell: (row) => <OwnerCell owner={row.owner} /> },
    {
      id: "why",
      header: t("docker.engine.lost.why"),
      card: "meta",
      cell: (row) => (row.why === "policy" ? t("docker.engine.lost.policy", { policy: row.policy }) : t("docker.engine.lost.stopped")),
    },
    { id: "fix", header: t("docker.engine.lost.fix"), card: "meta", hideBelow: "md", cell: (row) => t(`docker.engine.lost.fixes.${row.fix === "via_owner" || row.fix === "bring_first" || row.fix === "set_policy" ? row.fix : "set_policy"}`) },
  ];
  return (
    <SurfaceSection title={t("docker.engine.lost.title")} icon={OBJECT_ICONS.container} description={t("docker.engine.lost.description")} flush>
      <DataTable caption={t("docker.engine.lost.caption")} columns={columns} rows={items} getRowId={(row) => row.name} embedded empty={<EmptyState variant="inline" title={t("docker.engine.lost.none")} />} />
    </SurfaceSection>
  );
}

export function EnginePage() {
  const t = useT();
  const engine = useQuery(dockerEngineQuery());
  const lost = useQuery({ ...dockerRestartLossQuery(), enabled: engine.data?.usable === true });
  const version = engine.data?.info.server_version ?? "";
  return (
    <NodeCapabilityGate capability={DOCKER_CAPABILITY}>
      <DetailPage
        header={{
          title: t("docker.engine.title"),
          description: t("docker.engine.description"),
          ...(engine.data !== undefined
            ? {
                status:
                  engine.data.usable && version !== "" ? (
                    <StatusPill state="running" label={t("docker.summary.version", { version })} size="md" />
                  ) : (
                    <StatusPill state={engine.data.state === "missing" ? "stopped" : "failed"} label={t(`docker.engine.states.${engine.data.state === "unreachable" || engine.data.state === "too_old" ? engine.data.state : "missing"}`)} size="md" />
                  ),
              }
            : {}),
        }}
      >
        <QueryState
          query={engine}
          sentences={{ loading: t("docker.engine.loading"), failed: t("docker.engine.failed"), refreshFailed: t("docker.engine.refreshFailed") }}
          skeleton={<Skeleton className="h-64 w-full" />}
        >
          {(data) => (
            <Sections surfaces>
              <SurfaceSection title={t("docker.engine.fits.title")} icon={OBJECT_ICONS.diagnose} description={t("docker.engine.fits.description")}>
                <div className="flex min-w-0 flex-col gap-4">
                  <EngineSentences sentences={data.facts} label={t("docker.engine.fits.found")} />
                  {data.limits.length > 0 ? (
                    <Subsection title={t("docker.engine.fits.limits")}>
                      <EngineSentences sentences={data.limits} label={t("docker.engine.fits.limits")} />
                    </Subsection>
                  ) : null}
                  {data.state === "missing" ? (
                    <p className="text-13 text-fg-muted">
                      {t.rich("docker.engine.fits.install", {
                        software: (
                          <Link key="software" to="/server/software" className="text-accent-fg underline-offset-2 hover:underline">
                            {t("docker.engine.fits.installLink")}
                          </Link>
                        ),
                      })}
                    </p>
                  ) : null}
                </div>
              </SurfaceSection>
              {data.info.installed ? (
                <SurfaceSection title={t("docker.engine.engineTitle")} icon={OBJECT_ICONS.engine}>
                  <KeyValueList items={engineFacts(data.info, t)} />
                </SurfaceSection>
              ) : null}
              {data.usable && lost.data?.items !== undefined && lost.data.items.length > 0 ? <RestartLoss items={lost.data.items} /> : null}
            </Sections>
          )}
        </QueryState>
      </DetailPage>
    </NodeCapabilityGate>
  );
}
