import { useQuery } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { useState } from "react";

import type { Destination, RemoteBackup, RemoteBackups } from "../../api/queries/backupDestinations";
import { remoteBackupsQuery } from "../../api/queries/backupDestinations";
import { appsQuery } from "../../api/queries/apps";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { appNameOf } from "../apps/data";
import { RestoreDialog } from "./RestoreDialog";
import { useDestinationActions } from "./useDestinationActions";

/**
 * Restoring straight from a destination: the same confirmation a local restore asks for (the
 * target's name, typed), with the backup downloaded from `destination` first.
 */
function RestoreFromDestinationDialog({
  destination,
  backup,
  open,
  onOpenChange,
}: {
  destination: string;
  backup: RemoteBackup;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  // A destination's folders are named after the application (dots as dashes), not its
  // domain: the application deployed here under that name is the natural target. None on
  // a replacement server that has not deployed it yet: the operator types the domain.
  const { data: apps } = useQuery(appsQuery());
  const knownDomain = apps?.apps.find((app) => appNameOf(app.domain) === backup.app_name)?.domain;
  const { restoreFromDestination } = useDestinationActions();
  return (
    <RestoreDialog
      // A new proposal once the applications arrive: the field starts from the known domain.
      key={knownDomain ?? ""}
      open={open}
      onOpenChange={onOpenChange}
      title={t("backups.restoreFromDestination.title", { destination })}
      description={t("backups.restoreFromDestination.description", { destination })}
      source={t.rich("backups.restoreFromDestination.source", { id: <Mono tone="default">{backup.backup_id}</Mono>, app: <Mono tone="default">{backup.app_name}</Mono> })}
      domain={knownDomain ?? ""}
      offerVerify={false}
      envDescription={t("backups.restoreFromDestination.envFromDownload")}
      pending={restoreFromDestination.isPending}
      error={restoreFromDestination.error}
      onReset={() => restoreFromDestination.reset()}
      onRestore={({ targetDomain, restoreEnv }) =>
        restoreFromDestination.mutate(
          {
            destination,
            backupId: backup.backup_id,
            appName: backup.app_name,
            // The backup's own domain needs no target; the sidecar says it, and the server checks
            // that it belongs to this folder.
            targetDomain: targetDomain === knownDomain ? undefined : targetDomain,
            restoreEnv,
          },
          { onSuccess: () => onOpenChange(false) },
        )
      }
    />
  );
}

/**
 * The query's four states, narrowed with early returns rather than a ternary chain so
 * `remote.data` is not read while it could still be undefined.
 */
function DestinationBrowseResult({
  remote,
  name,
  app,
  columns,
  onSelectApp,
  onRestore,
  t,
}: {
  remote: ReturnType<typeof useQuery<RemoteBackups>>;
  name: string;
  app: string;
  columns: readonly Column<RemoteBackup>[];
  onSelectApp: (app: string) => void;
  onRestore: (backup: RemoteBackup) => void;
  t: T;
}) {
  if (remote.isPending) {
    return (
      <div aria-hidden="true" className="flex flex-col gap-2">
        <Skeleton className="h-9 rounded-card" />
        <Skeleton className="h-9 rounded-card" />
        <Skeleton className="h-9 rounded-card" />
      </div>
    );
  }
  if (remote.isError) {
    return (
      <ErrorBlock
        compact
        error={remote.error}
        title={t("backups.browseDialog.readError")}
        onRetry={() => void remote.refetch()}
        retrying={remote.isRefetching}
      />
    );
  }
  // The API always answers both `apps` and `backups`, one of them empty: which one to read is
  // decided by whether an application was asked for, not by which key came back populated.
  if (app.trim() !== "") {
    return (
      <DataTable
        columns={columns}
        rows={remote.data.backups ?? []}
        getRowId={(row) => row.backup_id}
        caption={t("backups.browseDialog.resultsCaption", { name, app })}
        empty={
          <EmptyState
            variant="inline"
            title={t("backups.browseDialog.noBackupsFound.title")}
            description={t("backups.browseDialog.noBackupsFound.description")}
          />
        }
        rowActions={(row) => (
          <Button size="sm" icon={<RotateCcw aria-hidden="true" />} onClick={() => onRestore(row)}>
            {t("backups.common.restore")}
          </Button>
        )}
        defaultSort={{ column: "modified", direction: "descending" }}
      />
    );
  }
  const apps = remote.data.apps ?? [];
  if (apps.length === 0) {
    return (
      <EmptyState
        variant="inline"
        title={t("backups.browseDialog.nothingFound.title")}
        description={t("backups.browseDialog.nothingFound.description")}
      />
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-13 text-fg-muted">{t("backups.browseDialog.appsFoundHint")}</p>
      <ul className="flex flex-col divide-y divide-border rounded-card border border-border">
        {apps.map((candidate) => (
          <li key={candidate}>
            <button
              type="button"
              onClick={() => onSelectApp(candidate)}
              className="flex w-full cursor-pointer items-center px-3 py-2 text-left text-13 text-fg -outline-offset-2 hover:bg-surface-hover"
            >
              <Mono>{candidate}</Mono>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export interface BrowseDestinationDialogProps {
  destinations: readonly Destination[];
  /** Preselects a destination, such as when opened from that destination's own row. */
  initialDestination?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** What a destination holds, browsed the way `noust backup destination browse` does it. */
export function BrowseDestinationDialog({ destinations, initialDestination, open, onOpenChange }: BrowseDestinationDialogProps) {
  const t = useT();
  const [name, setName] = useState(initialDestination ?? destinations[0]?.name ?? "");
  const [app, setApp] = useState("");
  const [restoring, setRestoring] = useState<RemoteBackup | null>(null);
  const remote = useQuery({ ...remoteBackupsQuery(name, app.trim() === "" ? null : app.trim()), enabled: open && name !== "" });

  const close = (next: boolean): void => {
    onOpenChange(next);
    if (!next) {
      setApp("");
      setRestoring(null);
    }
  };

  const columns: Column<RemoteBackup>[] = [
    { id: "backup_id", header: t("backups.browseDialog.columns.backup"), mono: true, cell: (row) => row.backup_id, sortValue: (row) => row.backup_id },
    {
      id: "modified",
      header: t("backups.browseDialog.columns.modified"),
      width: "w-36",
      cell: (row) => <RelativeTime value={row.modified} />,
      sortValue: (row) => row.modified ?? "",
    },
    {
      id: "size",
      header: t("backups.history.columns.size"),
      align: "end",
      mono: true,
      width: "w-24",
      cell: (row) => (row.size === null || row.size === undefined ? <EmptyCell /> : formatBytes(row.size, t.locale)),
      sortValue: (row) => row.size ?? -1,
    },
  ];

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={close}
        size="lg"
        title={t("backups.browseDialog.title")}
        description={t("backups.browseDialog.description")}
        footer={<Button onClick={() => close(false)}>{t("backups.common.close")}</Button>}
      >
        <div className="flex flex-col gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("backups.fields.destination")} nativeLabel={false}>
              <Select
                aria-label={t("backups.fields.destination")}
                value={name}
                onValueChange={(next) => {
                  setName(next);
                  setApp("");
                }}
                options={destinations.map((destination) => ({ value: destination.name, label: destination.name }))}
                disabled={destinations.length === 0}
              />
            </Field>
            <Field label={t("backups.fields.application")} optional description={t("backups.browseDialog.applicationDescription")}>
              <Input mono value={app} onValueChange={setApp} placeholder="shop-example-com" autoComplete="off" spellCheck={false} />
            </Field>
          </div>

          {name === "" ? (
            <EmptyState
              variant="inline"
              title={t("backups.browseDialog.noDestinations.title")}
              description={t("backups.browseDialog.noDestinations.description")}
            />
          ) : (
            <DestinationBrowseResult remote={remote} name={name} app={app} columns={columns} onSelectApp={setApp} onRestore={setRestoring} t={t} />
          )}
        </div>
      </Dialog>
      {restoring !== null ? (
        <RestoreFromDestinationDialog
          destination={name}
          backup={restoring}
          open
          onOpenChange={(next) => {
            if (!next) setRestoring(null);
          }}
        />
      ) : null}
    </>
  );
}
