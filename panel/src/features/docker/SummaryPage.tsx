/**
 * Server > Docker > Summary: what asks for the operator first (a container that keeps falling, one that stopped when it should
 * run, an image with a new version, a disk that is filling), the size of what Docker has, and what runs, by stack, with whose each
 * thing is. Nothing here is a number to read before it is a sentence to act on.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode } from "react";

import { dockerContainersQuery, dockerKeys, dockerStacksQuery, dockerSummaryQuery } from "../../api/queries/docker";
import type { DockerAttention, DockerContainer, DockerStack } from "../../api/queries/docker";
import { DashboardPage } from "../../components/page/DashboardPage";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Sections } from "../../components/page/Section";
import { StatTile } from "../../components/page/StatTile";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { Can } from "../../components/page/permissions";
import { Badge } from "../../components/ui/Badge";
import { Button, buttonClassName } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS, VERB_ICONS } from "../../components/ui/objectIcons";
import { Skeleton, SkeletonRows } from "../../components/ui/Skeleton";
import { StatusGlyph, StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { NodeCapabilityGate } from "../../nodes/capability";
import { useContainerVerb } from "./actions";
import { useJobInHand } from "../jobs/useJobInHand";
import { diskFacts, loopWhy, stoppedWhy, versionFacts } from "./attention";
import { useLookForNewVersions } from "./useLookForNewVersions";
import { CleanupDialog } from "./CleanupDialog";
import { ownerOf } from "./model";
import { ContainerLink, OwnerCell, StackLink, StackPill, ContainerPill } from "./pieces";
import { Unavailable } from "./Unavailable";

/** The capability a node must publish for the zone to exist on it (a Noust older than 4.3 does not). */
export const DOCKER_CAPABILITY = "GET /api/docker/summary";

function AttentionRow({ glyph, title, line, actions }: { glyph: "failed" | "warning" | "queued"; title: ReactNode; line: ReactNode; actions: ReactNode }) {
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3">
      <div className="flex min-w-0 flex-1 basis-80 items-start gap-3">
        <span className="flex h-5 shrink-0 items-center">
          <StatusGlyph state={glyph} size={14} className={glyph === "failed" ? "text-fail" : "text-warn"} />
        </span>
        <div className="min-w-0">
          <p className="text-14 font-medium text-pretty text-fg">{title}</p>
          <p className="mt-0.5 max-w-measure text-13 text-pretty text-fg-muted">{line}</p>
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
    </li>
  );
}

/** How a container that keeps falling ended, in one sentence. */
function loopSentence(t: T, attention: DockerAttention): string {
  const why = loopWhy(attention.facts);
  if (why.kind === "oom") {
    return why.limitBytes !== null
      ? t("docker.attention.loop.oomLimit", { code: why.code ?? 137, limit: formatBytes(why.limitBytes, t.locale), count: why.restarts, minutes: why.minutes })
      : t("docker.attention.loop.oom", { code: why.code ?? 137, count: why.restarts, minutes: why.minutes });
  }
  if (why.kind === "killed") return t("docker.attention.loop.killed", { count: why.restarts, minutes: why.minutes });
  return why.code !== null ? t("docker.attention.loop.errorCode", { code: why.code, count: why.restarts, minutes: why.minutes }) : t("docker.attention.loop.error", { count: why.restarts, minutes: why.minutes });
}

/** How a container that is not running ended, in one sentence, and what follows from its restart policy. */
function stoppedSentence(t: T, attention: DockerAttention): ReactNode {
  const why = stoppedWhy(attention.facts);
  const when = why.finishedAt !== null ? <RelativeTime value={why.finishedAt} /> : null;
  const ended =
    why.kind === "terminated"
      ? t.rich("docker.attention.stopped.terminated", { when: when ?? "" })
      : why.kind === "ended"
        ? t.rich("docker.attention.stopped.ended", { when: when ?? "" })
        : why.code !== null
          ? t.rich("docker.attention.stopped.code", { code: why.code, when: when ?? "" })
          : t.rich("docker.attention.stopped.unknown", { when: when ?? "" });
  return (
    <>
      {ended}
      {why.policy === "no" ? ` ${t("docker.attention.stopped.noPolicy")}` : ""}
    </>
  );
}

/** The rows of what needs the operator, worst first: the server decides the order and this only draws it. */
export function Attention({ items, onCleanup, fleet = false }: { items: readonly (DockerAttention & { node?: string })[]; onCleanup: () => void; fleet?: boolean }) {
  const t = useT();
  // Across the fleet a row says which server it is on, and its links choose that server; what changes Docker is that server's, on its page.
  const server = (item: { node?: string }): ReactNode =>
    fleet && item.node !== undefined ? (
      <Badge className="mr-2 align-middle">
        <Mono>{item.node}</Mono>
      </Badge>
    ) : null;
  const on = (item: { node?: string }): { node: string } | undefined => (fleet && item.node !== undefined ? { node: item.node } : undefined);
  return (
    <ul aria-label={t("docker.attention.listLabel")} className="m-0 flex list-none flex-col divide-y divide-border p-0">
      {items.map((item, index) => {
        const key = `${item.kind}-${item.subject ?? String(index)}`;
        if (item.kind === "loop" && item.subject !== null && item.subject !== undefined) {
          const subject = item.subject;
          return (
            <AttentionRow
              key={key}
              glyph="failed"
              title={
                <>
                  {server(item)}
                  {t.rich("docker.attention.loop.title", { name: <Mono key="name">{subject}</Mono> })}
                </>
              }
              line={
                <>
                  {loopSentence(t, item)} {item.owner !== null && item.owner !== undefined ? <OwnerCell owner={item.owner} /> : null}
                </>
              }
              actions={
                <Link to="/server/docker/containers/$ref" params={{ ref: subject }} {...(on(item) !== undefined ? { search: on(item) as never } : {})} className={buttonClassName("secondary", "sm")}>
                  {t("docker.attention.loop.action")}
                </Link>
              }
            />
          );
        }
        if (item.kind === "stopped" && item.subject !== null && item.subject !== undefined) {
          const subject = item.subject;
          const owner = item.owner === null || item.owner === undefined ? null : ownerOf(item.owner);
          return (
            <AttentionRow
              key={key}
              glyph="warning"
              title={
                <>
                  {server(item)}
                  {t.rich(stoppedWhy(item.facts).policy === "no" ? "docker.attention.stopped.titleFailed" : "docker.attention.stopped.title", { name: <Mono key="name">{subject}</Mono> })}
                </>
              }
              line={stoppedSentence(t, item)}
              actions={
                <>
                  {owner?.kind === "nobody" && !fleet ? <StartButton name={subject} /> : null}
                  <Link to="/server/docker/containers/$ref" params={{ ref: subject }} {...(on(item) !== undefined ? { search: on(item) as never } : {})} className={buttonClassName("ghost", "sm")}>
                    {t("docker.attention.stopped.action")}
                  </Link>
                </>
              }
            />
          );
        }
        if ((item.kind === "update" || item.kind === "major") && item.count > 0) {
          const facts = versionFacts(item.facts);
          const major = item.kind === "major";
          const first = facts.images[0];
          return (
            <AttentionRow
              key={key}
              glyph={major ? "warning" : "queued"}
              title={
                <>
                  {server(item)}
                  {major
                    ? first !== undefined && item.count === 1
                      ? t.rich("docker.attention.major.titleOne", { image: <Mono key="image">{first}</Mono> })
                      : t("docker.attention.major.title", { count: item.count })
                    : t("docker.attention.update.title", { count: item.count })}
                </>
              }
              line={
                <>
                  {major ? t("docker.attention.major.line") : t("docker.attention.update.line")}
                  {facts.checkedAt !== null ? (
                    <>
                      {" "}
                      <RelativeTime value={facts.checkedAt} className="text-fg-faint" />
                    </>
                  ) : null}
                </>
              }
              actions={
                <Link to="/server/docker/images" search={{ news: true, ...(on(item) ?? {}) } as never} className={buttonClassName("secondary", "sm")}>
                  {t("docker.attention.update.action", { count: item.count })}
                </Link>
              }
            />
          );
        }
        if (item.kind === "disk") {
          const disk = diskFacts(item.facts);
          return (
            <AttentionRow
              key={key}
              glyph="warning"
              title={
                <>
                  {server(item)}
                  {disk.free !== null && disk.total !== null ? t("docker.attention.disk.title", { free: formatBytes(disk.free, t.locale), total: formatBytes(disk.total, t.locale) }) : t("docker.attention.disk.titleNoNumbers")}
                </>
              }
              line={disk.reclaimable !== null && disk.reclaimable > 0 ? t("docker.attention.disk.line", { size: formatBytes(disk.reclaimable, t.locale) }) : t("docker.attention.disk.lineNothing")}
              actions={
                fleet ? (
                  <Link to="/server/docker" {...(on(item) !== undefined ? { search: on(item) as never } : {})} className={buttonClassName("secondary", "sm")}>
                    {t("docker.fleet.openServer")}
                  </Link>
                ) : (
                  <Can permission="docker.manage">
                    <Button size="sm" variant="secondary" icon={<VERB_ICONS.empty />} onClick={onCleanup}>
                      {t("docker.attention.disk.action")}
                    </Button>
                  </Can>
                )
              }
            />
          );
        }
        return null;
      })}
    </ul>
  );
}

/** Starts a container nobody of Noust made (an application's is started from the application). */
function StartButton({ name }: { name: string }) {
  const t = useT();
  return (
    <Can permission="docker.manage">
      <StartAction name={name} label={t("common.words.start")} />
    </Can>
  );
}

function StartAction({ name, label }: { name: string; label: string }) {
  const start = useContainerVerb("start");
  return (
    <Button size="sm" variant="secondary" loading={start.isPending} onClick={() => start.mutate(name)}>
      {label}
    </Button>
  );
}

/** What the table "What runs" holds a row of: a stack, or a container that belongs to none. */
type Row = { kind: "stack"; stack: DockerStack } | { kind: "loose"; container: DockerContainer };

function rowsOf(stacks: readonly DockerStack[], containers: readonly DockerContainer[]): Row[] {
  const rows: Row[] = stacks.map((stack) => ({ kind: "stack", stack }));
  for (const container of containers) if (container.project === "") rows.push({ kind: "loose", container });
  return rows;
}

function WhatRuns({ stacks, containers }: { stacks: readonly DockerStack[] | undefined; containers: readonly DockerContainer[] | undefined }) {
  const t = useT();
  const rows = stacks === undefined || containers === undefined ? undefined : rowsOf(stacks, containers);
  const columns: Column<Row>[] = [
    { id: "name", header: t("docker.summary.runs.name"), card: "title", cell: (row) => (row.kind === "stack" ? <StackLink name={row.stack.name} /> : <ContainerLink name={row.container.name} />) },
    {
      id: "state",
      header: t("docker.columns.state"),
      card: "status",
      width: "w-40",
      cell: (row) => (row.kind === "stack" ? <StackPill state={row.stack.state} /> : <ContainerPill kind={row.container.state_kind} />),
    },
    {
      id: "count",
      header: t("docker.columns.containers"),
      align: "end",
      mono: true,
      width: "w-32",
      hideBelow: "sm",
      cell: (row) => (row.kind === "stack" ? `${String(row.stack.running)}/${String(row.stack.total)}` : "1"),
    },
    { id: "owner", header: t("docker.columns.owner"), card: "meta", cell: (row) => <OwnerCell owner={row.kind === "stack" ? row.stack.owner : row.container.owner} /> },
  ];
  return (
    <SurfaceSection
      title={t("docker.summary.runs.title")}
      icon={OBJECT_ICONS.compose}
      description={t("docker.summary.runs.description")}
      actions={
        <Link to="/server/docker/stacks" className={buttonClassName("ghost", "sm")}>
          {t("docker.summary.runs.all")}
        </Link>
      }
      flush
    >
      <DataTable
        caption={t("docker.summary.runs.caption")}
        columns={columns}
        rows={rows}
        getRowId={(row) => (row.kind === "stack" ? `stack:${row.stack.name}` : `container:${row.container.name}`)}
        skeletonRows={4}
        embedded
        empty={<EmptyState variant="inline" title={t("docker.summary.runs.empty")} />}
      />
    </SurfaceSection>
  );
}

function Figures({ counts, disk }: { counts: Record<string, Record<string, number>>; disk: { docker_bytes?: number | null; reclaimable_bytes?: number | null } | null | undefined }) {
  const t = useT();
  const containers = counts["containers"] ?? {};
  const stacks = counts["stacks"] ?? {};
  const images = counts["images"] ?? {};
  const used = disk?.docker_bytes ?? null;
  const reclaimable = disk?.reclaimable_bytes ?? 0;
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <StatTile
        label={t("docker.summary.tiles.containers")}
        value={containers["total"] ?? 0}
        detail={t("docker.summary.tiles.containersLine", { running: containers["running"] ?? 0, failing: containers["failing"] ?? 0, stopped: containers["stopped"] ?? 0 })}
        detailLines={2}
      />
      <StatTile label={t("docker.summary.tiles.stacks")} value={stacks["total"] ?? 0} detail={t("docker.summary.tiles.stacksLine", { count: stacks["unowned"] ?? 0 })} detailLines={2} />
      <StatTile label={t("docker.summary.tiles.images")} value={images["total"] ?? 0} detail={t("docker.summary.tiles.imagesLine")} detailLines={2} />
      <StatTile
        label={t("docker.summary.tiles.disk")}
        value={used === null ? "-" : formatBytes(used, t.locale)}
        detail={reclaimable > 0 ? t("docker.summary.tiles.diskLine", { size: formatBytes(reclaimable, t.locale) }) : t("docker.summary.tiles.diskNothing")}
        detailLines={2}
      />
    </div>
  );
}

function Skeletons() {
  return (
    <Sections surfaces>
      <SkeletonRows rows={3} />
      <Skeleton className="h-24 w-full" />
    </Sections>
  );
}

export function SummaryPage() {
  const t = useT();
  const summary = useQuery(dockerSummaryQuery());
  const available = summary.data?.available === true;
  const stacks = useQuery({ ...dockerStacksQuery(), enabled: available });
  const containers = useQuery({ ...dockerContainersQuery(), enabled: available });
  const [cleaning, setCleaning] = useState(false);
  const queryClient = useQueryClient();
  const look = useLookForNewVersions();
  // The cleanup is a job: it is followed where it was asked, and what it removed is read again when it ends.
  const job = useJobInHand(() => {
    void queryClient.invalidateQueries({ queryKey: dockerKeys.all });
  });
  const engine = summary.data?.engine;
  const version = engine?.info.server_version ?? "";
  const items = summary.data?.attention ?? [];
  return (
    <NodeCapabilityGate capability={DOCKER_CAPABILITY}>
      <DashboardPage
        header={{
          title: t("docker.summary.title"),
          description: t("docker.summary.description"),
          ...(available && version !== "" ? { status: <StatusPill state="running" label={t("docker.summary.version", { version })} size="md" /> } : {}),
          ...(available
            ? {
                secondaryActions: (
                  <>
                    {look.button}
                    <Can permission="docker.manage">
                      <Button variant="secondary" icon={<VERB_ICONS.empty aria-hidden="true" />} onClick={() => setCleaning(true)}>
                        {t("docker.cleanup.open")}
                      </Button>
                    </Can>
                  </>
                ),
                primaryAction: (
                  <Link to="/apps/new" className={buttonClassName("primary")}>
                    <ICONS.add aria-hidden="true" />
                    {t("apps.newApplication")}
                  </Link>
                ),
              }
            : {}),
        }}
      >
        <QueryState
          query={summary}
          sentences={{ loading: t("docker.summary.loading"), failed: t("docker.summary.failed"), refreshFailed: t("docker.summary.refreshFailed") }}
          skeleton={<Skeletons />}
        >
          {(data) =>
            !data.available ? (
              <Unavailable engine={data.engine} />
            ) : (
              <Sections surfaces>
                {look.notice}
                {job.notice}
                <SurfaceSection title={t("docker.attention.title")} icon={OBJECT_ICONS.alert} {...(items.length > 0 ? { description: t("docker.attention.description") } : {})} flush>
                  {items.length > 0 ? (
                    <Attention items={items} onCleanup={() => setCleaning(true)} />
                  ) : (
                    <div className="px-5 py-3">
                      <EmptyState variant="inline" title={t("docker.attention.nothing")} />
                    </div>
                  )}
                </SurfaceSection>
                <Figures counts={data.counts ?? {}} disk={data.disk} />
                <WhatRuns stacks={stacks.data?.items} containers={containers.data?.items} />
              </Sections>
            )
          }
        </QueryState>
      </DashboardPage>
      <CleanupDialog
        open={cleaning}
        onOpenChange={setCleaning}
        onQueued={(accepted) => job.track(accepted, { running: t("docker.cleanup.running"), succeeded: t("docker.cleanup.succeeded"), failed: t("docker.cleanup.jobFailed") })}
      />
    </NodeCapabilityGate>
  );
}
