/**
 * The disks: every real filesystem (bind mounts and pseudo filesystems left out) by how full it
 * is, what takes the space with what can be given back, cleaning it with its plan read first,
 * and swap.
 *
 * Cleaning is a closed list of actions; nothing here takes a path. Measuring the known places is
 * a job with a deadline per path, and the page says how old its answer is.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { CommandHint } from "../../../components/page/CommandHint";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Drawer } from "../../../components/ui/Drawer";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Meter } from "../../../components/ui/Progress";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes, formatPercent } from "../../../lib/format";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { ActionDialog } from "../ActionDialog";
import { ServerErrorBlock, explainServerError } from "../errors";
import { SERVER_CAPABILITY, cleanupPlanQuery, dockerImagesQuery, serverKeys, storageQuery } from "../queries";
import type { Candidate, Mount, Storage } from "../queries";
import { useServerJob } from "../serverJob";
import { TabToolbar } from "../TabToolbar";
import { SwapCard } from "./SwapCard";

/** What a candidate is, by the id the API gives it; one this console does not know keeps its id. */
export function candidateLabel(t: T, id: string): string {
  switch (id) {
    case "journal":
      return t("server.storage.candidates.journal");
    case "docker-build-cache":
      return t("server.storage.candidates.dockerBuildCache");
    case "docker-images":
      return t("server.storage.candidates.dockerImages");
    case "docker-volumes":
      return t("server.storage.candidates.dockerVolumes");
    case "pkg-cache":
    case "pkg-cache-dnf":
    case "pkg-cache-libdnf5":
    case "pkg-cache-zypp":
      return t("server.storage.candidates.pkgCache");
    case "releases":
      return t("server.storage.candidates.releases");
    case "backups":
      return t("server.storage.candidates.backups");
    case "noust-logs":
      return t("server.storage.candidates.noustLogs");
    case "crash":
    case "coredump":
      return t("server.storage.candidates.crash");
    case "tmp":
    case "var-tmp":
      return t("server.storage.candidates.tmp");
    case "postgresql-data":
    case "mysql-data":
    case "mongodb-data":
      return t("server.storage.candidates.databases");
    default:
      return id;
  }
}

/** Biggest first: what to look at first. */
export function sortCandidates(candidates: readonly Candidate[]): Candidate[] {
  return [...candidates].sort((a, b) => (b.size_bytes ?? -1) - (a.size_bytes ?? -1));
}

/** Filesystems a VPS usually has (the root, a boot partition, a data volume): the loading shape. */
const TYPICAL_MOUNTS = 3;
/** Places the scan usually reports: the loading shape of the table. */
const TYPICAL_CANDIDATES = 8;

function Mounts({ mounts }: { mounts: readonly Mount[] | undefined }) {
  const t = useT();
  return (
    <Card level={2} title={t("server.storage.mountsTitle")} padding="none">
      {mounts === undefined ? (
        <ul aria-busy="true" aria-label={t("server.storage.loading")} className="flex flex-col divide-y divide-border">
          {Array.from({ length: TYPICAL_MOUNTS }, (_, index) => (
            <li key={index} className="grid min-w-0 gap-x-6 gap-y-2 px-5 py-3 md:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-3 w-40" />
              </div>
              <div className="flex flex-col gap-1.5">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-1.5 w-full" />
              </div>
            </li>
          ))}
        </ul>
      ) : mounts.length === 0 ? (
        <div className="px-5 pb-4">
          <EmptyState variant="inline" title={t("server.storage.noMounts")} />
        </div>
      ) : (
        <ul aria-label={t("server.storage.mountsLabel")} className="flex flex-col divide-y divide-border">
          {mounts.map((mount) => (
            <li key={mount.mount_point} className="grid min-w-0 gap-x-6 gap-y-2 px-5 py-3 md:grid-cols-2">
              <div className="flex min-w-0 flex-col gap-0.5">
                <span className="flex min-w-0 flex-wrap items-center gap-2 text-13">
                  <Mono truncate>{mount.mount_point}</Mono>
                  {mount.readonly ? <Badge>{t("server.storage.readonly")}</Badge> : null}
                </span>
                <span className="flex min-w-0 flex-wrap gap-x-2 text-12 text-fg-muted">
                  <Mono tone="muted" truncate>
                    {mount.device}
                  </Mono>
                  <Mono tone="faint">{mount.fstype}</Mono>
                  {mount.inodes_percent >= 50 ? <span>{t("server.storage.inodes", { percent: formatPercent(mount.inodes_percent, t.locale) })}</span> : null}
                </span>
              </div>
              <Meter
                value={mount.used_bytes}
                max={mount.total_bytes}
                label={t("server.storage.free", { free: formatBytes(mount.free_bytes, t.locale), total: formatBytes(mount.total_bytes, t.locale) })}
                valueText={formatPercent(mount.percent_used, t.locale)}
              />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

type Opened = { kind: "clean"; candidate: Candidate } | { kind: "images" } | null;

function CleanupDialog({ action, onClose }: { action: string; onClose: () => void }) {
  const t = useT();
  const jobs = useServerJob();
  const [size, setSize] = useState("200");
  const journal = action === "journal";
  const sizeMb = journal && /^\d+$/.test(size) ? Number(size) : null;
  const plan = useQuery(cleanupPlanQuery(action, sizeMb));
  return (
    <ActionDialog
      title={t("server.storage.cleanTitle", { what: candidateLabel(t, action) })}
      description={t("server.storage.cleanDescription")}
      actionLabel={t("server.storage.cleanAction")}
      destructive={plan.data?.needs_confirmation === true}
      onClose={onClose}
      onConfirm={async () => {
        const accepted = await request("post", "/api/server/storage/cleanup", {
          body: { action, confirm: plan.data?.needs_confirmation === true, ...(sizeMb !== null ? { size_mb: sizeMb } : {}) },
        });
        jobs.track(accepted.job_id, "cleanup");
      }}
    >
      {journal ? (
        <Field label={t("server.storage.journalSize")} description={t("server.storage.journalSizeHelp")}>
          <Input mono inputMode="numeric" value={size} onValueChange={(value: string) => setSize(value)} suffix={<span className="px-2 text-12 text-fg-faint">MB</span>} className="w-40" />
        </Field>
      ) : null}
      {plan.isError ? (
        <ServerErrorBlock compact error={plan.error} title={t("server.storage.planFailed")} />
      ) : plan.data === undefined ? (
        <Skeleton className="h-16 w-full rounded-control" />
      ) : (
        <div className="flex flex-col gap-3">
          {/* What the action takes, in Noust's words. */}
          <p className="text-13 text-fg">{plan.data.effect}</p>
          {(plan.data.items ?? []).length > 0 ? (
            <ul aria-label={t("server.storage.itemsLabel")} className="flex flex-col gap-0.5 text-12">
              {(plan.data.items ?? []).map((item) => (
                <li key={item}>
                  <Mono>{item}</Mono>
                </li>
              ))}
            </ul>
          ) : null}
          {plan.data.commands.map((command) => (
            <CommandHint key={command} command={command} label={t("server.storage.runs")} />
          ))}
        </div>
      )}
    </ActionDialog>
  );
}

function ImagesDrawer({ onClose }: { onClose: () => void }) {
  const t = useT();
  const jobs = useServerJob();
  const images = useQuery(dockerImagesQuery());
  const [removing, setRemoving] = useState<string | null>(null);
  const rows = images.data ?? [];
  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="lg"
      title={t("server.storage.imagesTitle")}
      description={t("server.storage.imagesDescription")}
    >
      {images.isError ? (
        <ServerErrorBlock compact error={images.error} title={t("server.storage.imagesFailed")} onRetry={() => void images.refetch()} />
      ) : (
        <DataTable
          columns={[
            {
              id: "image",
              header: t("server.storage.imageColumn"),
              card: "title",
              cell: (row) => (
                <span className="flex min-w-0 flex-col">
                  <Mono truncate>{`${row.repository}:${row.tag}`}</Mono>
                  <Mono tone="faint">{row.id}</Mono>
                </span>
              ),
            },
            { id: "size", header: t("server.storage.sizeColumn"), align: "end", mono: true, cell: (row) => <Mono>{row.size}</Mono> },
          ]}
          rows={rows}
          getRowId={(row) => row.id}
          caption={t("server.storage.imagesCaption")}
          loading={images.isPending}
          skeletonRows={3}
          mobile="cards"
          empty={<EmptyState variant="inline" title={t("server.storage.noImages")} />}
          rowActions={(row) => (
            <Button size="sm" variant="ghost" onClick={() => setRemoving(row.id)} aria-label={t("server.storage.removeImage", { image: `${row.repository}:${row.tag}` })}>
              {t("server.storage.remove")}
            </Button>
          )}
        />
      )}
      {removing !== null ? (
        <ActionDialog
          title={t("server.storage.removeImageTitle")}
          description={t("server.storage.removeImageDescription")}
          actionLabel={t("server.storage.removeImageAction")}
          destructive
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            const accepted = await request("post", "/api/server/storage/cleanup", { body: { action: "docker-image", target: removing, confirm: true } });
            jobs.track(accepted.job_id, "cleanup");
          }}
        >
          <p className="text-13">
            <Mono>{removing}</Mono>
          </p>
        </ActionDialog>
      ) : null}
    </Drawer>
  );
}

function Candidates({ storage, open }: { storage: Storage | undefined; open: (opened: Opened) => void }) {
  const t = useT();
  const columns: Column<Candidate>[] = [
    {
      id: "what",
      header: t("server.storage.what"),
      card: "title",
      cell: (row) => (
        <span className="flex min-w-0 flex-col">
          <span className="text-13 text-fg">{candidateLabel(t, row.id)}</span>
          {/* The path or what Noust found, verbatim. */}
          {row.detail !== "" ? <span className="truncate text-12 text-fg-muted" title={row.detail}>{row.detail}</span> : null}
        </span>
      ),
    },
    {
      id: "size",
      header: t("server.storage.size"),
      align: "end",
      mono: true,
      cell: (row) => (row.size_bytes != null ? formatBytes(row.size_bytes, t.locale) : <EmptyCell reason={t("server.storage.notMeasured")} />),
      sortValue: (row) => row.size_bytes ?? null,
    },
    {
      id: "reclaimable",
      header: t("server.storage.reclaimable"),
      align: "end",
      mono: true,
      hideBelow: "sm",
      cell: (row) => (row.reclaimable_bytes != null ? formatBytes(row.reclaimable_bytes, t.locale) : <EmptyCell reason={t("server.storage.nothingToReclaim")} />),
    },
    {
      id: "measured",
      header: t("server.storage.measured"),
      hideBelow: "lg",
      cell: (row) => (row.measured_at ? <RelativeTime value={row.measured_at} /> : <EmptyCell reason={t("server.storage.notMeasured")} />),
    },
  ];
  return (
    <Card level={2} title={t("server.storage.candidatesTitle")} description={t("server.storage.candidatesDescription")} padding="none">
      <DataTable
        columns={columns}
        rows={storage ? sortCandidates(storage.candidates) : []}
        loading={storage === undefined}
        skeletonRows={TYPICAL_CANDIDATES}
        // One id can measure several places (Noust's logs are three directories).
        getRowId={(row) => `${row.id}:${row.detail}`}
        caption={t("server.storage.candidatesCaption")}
        mobile="cards"
        empty={<EmptyState variant="inline" title={t("server.storage.noCandidates")} />}
        rowActions={(row) =>
          row.id === "docker-images" ? (
            <Button size="sm" variant="ghost" onClick={() => open({ kind: "images" })}>
              {t("server.storage.review")}
            </Button>
          ) : row.action != null && row.reclaimable_bytes !== 0 ? (
            <Button size="sm" variant="ghost" onClick={() => open({ kind: "clean", candidate: row })}>
              {t("server.storage.clean")}
            </Button>
          ) : null
        }
      />
    </Card>
  );
}

function StorageView() {
  const t = useT();
  const { node } = useNode();
  const jobs = useServerJob();
  const queryClient = useQueryClient();
  const storage = useQuery(storageQuery());
  const [opened, setOpened] = useState<Opened>(null);
  const analyze = useMutation({
    mutationFn: () => request("post", "/api/server/storage/analyze"),
    onSuccess: (accepted) => {
      jobs.track(accepted.job_id, "analyze");
      void queryClient.invalidateQueries({ queryKey: serverKeys.storage });
    },
    onError: (error) => reportActionError(t("server.job.analyze.failed"), explainServerError(t, error, node)),
  });

  if (storage.isError && storage.data === undefined) {
    return <ServerErrorBlock error={storage.error} title={t("server.storage.loadFailed")} onRetry={() => void storage.refetch()} retrying={storage.isRefetching} />;
  }
  const data = storage.data;
  const worst = data?.worst ?? null;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <TabToolbar
        summary={
          data === undefined ? (
            <Skeleton className="h-5 w-72" />
          ) : worst !== null ? (
            t.rich("server.storage.summary", {
              mount: <Mono tone="default">{worst.mount_point}</Mono>,
              percent: formatPercent(worst.percent_used, t.locale),
              free: formatBytes(worst.free_bytes, t.locale),
            })
          ) : (
            t("server.storage.summaryNone")
          )
        }
        actions={
          <Button loading={analyze.isPending} disabled={jobs.busy} onClick={() => analyze.mutate()}>
            {data?.analysis_at ? t("server.storage.measureAgain") : t("server.storage.measure")}
          </Button>
        }
      />
      <Mounts mounts={data?.mounts} />
      <div className="grid min-w-0 items-start gap-6 xl:grid-cols-3">
        <div className="flex min-w-0 flex-col gap-2 xl:col-span-2">
          <Candidates storage={data} open={setOpened} />
          <p className="min-h-4 text-12 text-fg-muted">
            {data === undefined ? null : data.analysis_at ? t.rich("server.storage.measuredAt", { when: <RelativeTime value={data.analysis_at} /> }) : t("server.storage.neverMeasured")}
          </p>
        </div>
        <SwapCard />
      </div>
      <CommandHint command="noust server storage" label={t("server.fromTerminal")} />
      {opened?.kind === "clean" && opened.candidate.action != null ? <CleanupDialog action={opened.candidate.action} onClose={() => setOpened(null)} /> : null}
      {opened?.kind === "images" ? <ImagesDrawer onClose={() => setOpened(null)} /> : null}
    </div>
  );
}

/** The Storage tab. */
export function StorageTab() {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      <StorageView />
    </NodeCapabilityGate>
  );
}

