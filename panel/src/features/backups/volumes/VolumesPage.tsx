import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Play } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { request } from "../../../api/client";
import { volumeBackupsQuery, volumeKeys, volumePoliciesQuery } from "../../../api/queries/volumeBackups";
import type { VolumePolicy } from "../../../api/queries/volumeBackups";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { RowMenu } from "../../../components/page/RowMenu";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { useJobInHand } from "../../jobs/useJobInHand";
import type { JobWords } from "../../jobs/useJobInHand";
import type { Accepted } from "../../databases/jobs";
import { BackupsListPage } from "../BackupsListPage";
import { STATE_RANK, scheduleWords, totalBytes, viewOf, volumeRows } from "./data";
import type { VolumeRow } from "./data";
import { VolumeDetailDrawer, coversWords } from "./VolumeDetailDrawer";
import { VolumePolicyDrawer } from "./VolumePolicyDrawer";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

export interface VolumesSearch {
  /** The application whose data is open in the drawer. */
  domain?: string;
}

export function validateVolumesSearch(search: Record<string, unknown>): VolumesSearch {
  const domain = typeof search["domain"] === "string" && search["domain"] !== "" ? search["domain"].slice(0, 253) : undefined;
  return domain !== undefined ? { domain } : {};
}

export interface VolumesPageProps {
  search: VolumesSearch;
  onSearchChange: (next: VolumesSearch, options?: { replace?: boolean }) => void;
}

type Editing = { domain: string | null; policy: VolumePolicy | null } | null;

/**
 * The Volumes tab: every application whose data is backed up on its own (its persistent folders
 * and the Docker volumes of its stack), with the state of its policy, what it backs up, when it
 * last did and how much it weighs. A row opens its data: the policy and every backup, to put
 * back, check, send or delete. A policy is made and edited in a drawer; running one, restoring
 * and the rest are jobs followed above the table.
 */
export function VolumesPage({ search, onSearchChange }: VolumesPageProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const policies = useQuery(volumePoliciesQuery());
  const backups = useQuery(volumeBackupsQuery());
  const refresh = useCallback(() => void queryClient.invalidateQueries({ queryKey: volumeKeys.all }), [queryClient]);
  const job = useJobInHand(refresh);
  const [editing, setEditing] = useState<Editing>(null);
  const [removing, setRemoving] = useState<VolumeRow | null>(null);

  const rows = useMemo(() => volumeRows(policies.data?.policies, backups.data?.backups), [policies.data, backups.data]);
  const loading = policies.isPending || backups.isPending;
  const taken = useMemo(() => new Set(rows.filter((row) => row.policy !== null).map((row) => row.domain)), [rows]);
  const open = search.domain === undefined ? null : (rows.find((row) => row.domain === search.domain) ?? { domain: search.domain, policy: null, backups: [], newest: null, state: "none" as const });

  const run = useMutation({
    mutationFn: async (row: VolumeRow) => {
      const accepted: Accepted =
        row.policy !== null
          ? await request("post", "/api/volume-backups/policies/{domain}/run", { params: { domain: row.domain } })
          : await request("post", "/api/volume-backups", { body: { domain: row.domain, targets: [], extra_paths: [], consistency: "live", description: "" } });
      return { accepted, domain: row.domain };
    },
    onSuccess: ({ accepted, domain }) => job.track(accepted, runWords(domain)),
    onError: (error) => reportActionError(t("volumeBackups.runFailed"), error),
  });

  function runWords(domain: string): JobWords {
    return { running: t("volumeBackups.job.running", { domain }), succeeded: t("volumeBackups.job.succeeded", { domain }), failed: t("volumeBackups.job.failed", { domain }) };
  }

  const openRow = (row: VolumeRow): void => onSearchChange({ domain: row.domain });
  const newPolicy = (): void => setEditing({ domain: null, policy: null });

  const columns: Column<VolumeRow>[] = [
    { id: "domain", header: t("volumeBackups.columns.application"), card: "title", sortValue: (row) => row.domain, cell: (row) => <Mono tone="default">{row.domain}</Mono> },
    {
      id: "state",
      header: t("volumeBackups.columns.state"),
      width: "w-44",
      card: "status",
      sortValue: (row) => STATE_RANK[row.state],
      cell: (row) => {
        const view = viewOf(row.state);
        return <StatusPill appearance="inline" size="sm" state={view.state} label={t(view.label)} />;
      },
    },
    {
      id: "schedule",
      header: t("volumeBackups.columns.schedule"),
      hideBelow: "md",
      cell: (row) => (row.policy === null ? <EmptyCell reason={t("volumeBackups.state.none")} /> : scheduleWords(row.policy.schedule, t.locale)),
    },
    {
      id: "covers",
      header: t("volumeBackups.columns.covers"),
      hideBelow: "lg",
      card: "hidden",
      cell: (row) => (row.policy === null ? <EmptyCell reason={t("volumeBackups.state.none")} /> : coversWords(t, row.policy.targets ?? [], row.policy.extra_paths ?? [])),
    },
    {
      id: "last",
      header: t("volumeBackups.columns.last"),
      width: "w-36",
      hideBelow: "sm",
      sortValue: (row) => (row.newest === null ? 0 : Date.parse(row.newest.created_at)),
      cell: (row) => (row.newest === null ? <span className="text-fg-muted">{t("volumeBackups.none")}</span> : <RelativeTime value={row.newest.created_at} />),
    },
    {
      id: "size",
      header: t("volumeBackups.columns.size"),
      width: "w-28",
      align: "end",
      mono: true,
      sortValue: (row) => totalBytes(row.backups),
      cell: (row) => (row.backups.length === 0 ? <EmptyCell reason={t("volumeBackups.none")} /> : formatBytes(totalBytes(row.backups), t.locale)),
    },
  ];

  let content;
  // design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed
  if (policies.isError && policies.data === undefined) {
    content = <ErrorBlock error={policies.error} title={t("volumeBackups.loadFailed")} onRetry={() => void policies.refetch()} retrying={policies.isRefetching} />;
  } else if (!loading && rows.length === 0) {
    content = (
      <EmptyState
        variant="firstUse"
        icon={<OBJECT_ICONS.volume />}
        title={t("volumeBackups.empty.title")}
        description={t("volumeBackups.empty.description")}
        action={
          <Button variant="primary" onClick={newPolicy}>
            {t("volumeBackups.empty.action")}
          </Button>
        }
        command="noust backup volumes schedule set shop.example.com --schedule daily"
      />
    );
  } else {
    content = (
      <DataTable<VolumeRow>
        caption={t("volumeBackups.caption")}
        columns={columns}
        rows={rows}
        loading={loading}
        skeletonRows={3}
        getRowId={(row) => row.domain}
        onRowActivate={openRow}
        rowActions={(row) => (
          <RowMenu name={row.domain}>
            <MenuItem icon={<Play />} disabled={job.busy || run.isPending} onClick={() => run.mutate(row)}>
              {t("common.words.backUpNow")}
            </MenuItem>
            <MenuItem icon={row.policy === null ? <OBJECT_ICONS.schedule /> : <Pencil />} onClick={() => setEditing({ domain: row.domain, policy: row.policy })}>{row.policy === null ? t("volumeBackups.detail.setPolicy") : t("volumeBackups.actions.edit")}</MenuItem>
            <MenuItem icon={<OBJECT_ICONS.backup />} onClick={() => openRow(row)}>{t("volumeBackups.actions.open")}</MenuItem>
            {row.policy !== null ? (
              <>
                <MenuSeparator />
                <MenuItem icon={<ICONS.delete />} destructive onClick={() => setRemoving(row)}>
                  {t("volumeBackups.actions.remove")}
                </MenuItem>
              </>
            ) : null}
          </RowMenu>
        )}
      />
    );
  }

  return (
    <BackupsListPage
      primaryAction={
        <Button variant="primary" icon={<ICONS.add aria-hidden="true" />} onClick={newPolicy}>
          {t("volumeBackups.newPolicy")}
        </Button>
      }
      {...(job.notice !== null ? { notice: job.notice } : {})}
      {...(rows.length > 0 ? { footer: <CommandHint command="noust backup volumes schedule set shop.example.com --schedule daily" label={t("common.words.fromTerminal")} /> } : {})}
    >
      {content}

      {open !== null ? (
        <VolumeDetailDrawer
          key={open.domain}
          row={open}
          open
          onOpenChange={(next) => (next ? undefined : onSearchChange({}, { replace: true }))}
          onEdit={() => setEditing({ domain: open.domain, policy: open.policy })}
          onRun={() => run.mutate(open)}
          onRemove={() => setRemoving(open)}
          onQueued={(accepted, words) => job.track(accepted, words)}
          busy={job.busy || run.isPending}
        />
      ) : null}
      {editing !== null ? (
        <VolumePolicyDrawer
          key={`${editing.domain ?? "new"}:${editing.policy?.updated_at ?? ""}`}
          domain={editing.domain}
          policy={editing.policy}
          taken={taken}
          open
          onOpenChange={(next) => (next ? undefined : setEditing(null))}
        />
      ) : null}
      {removing !== null ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(next) => (next ? undefined : setRemoving(null))}
          title={t("volumeBackups.detail.removeTitle", { domain: removing.domain })}
          description={t("volumeBackups.detail.removeDescription")}
          actionLabel={t("volumeBackups.detail.removeAction")}
          server={node}
          onConfirm={async () => {
            await request("delete", "/api/volume-backups/policies/{domain}", { params: { domain: removing.domain } });
            refresh();
          }}
        />
      ) : null}
    </BackupsListPage>
  );
}
