import { AlertDialog } from "@base-ui/react/alert-dialog";
import { useQuery } from "@tanstack/react-query";
import { FolderOpen, RotateCcw } from "lucide-react";
import { useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import type { Destination, RemoteBackup, RemoteBackups } from "../../api/queries/backupDestinations";
import { remoteBackupsQuery } from "../../api/queries/backupDestinations";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { BACKDROP, Dialog, DialogFrame, MODAL_POPUP, MODAL_VIEWPORT } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { cx } from "../../lib/cx";
import { formatBytes } from "../../lib/format";
import { useDestinationActions } from "./useDestinationActions";

/**
 * Restoring straight from a destination: type-to-confirm the target domain, the same
 * confirmation `RestoreBackupDialog` asks a local restore for. The backup is downloaded from
 * `destination` first, then replayed the same way.
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
  const inputRef = useRef<HTMLInputElement>(null);
  const [targetDomain, setTargetDomain] = useState(backup.app_name);
  const [typed, setTyped] = useState("");
  const [restoreEnv, setRestoreEnv] = useState(true);
  const { restoreFromDestination } = useDestinationActions();

  const matches = typed === targetDomain && targetDomain.trim() !== "";

  const close = (next: boolean): void => {
    if (!next && restoreFromDestination.isPending) return;
    onOpenChange(next);
    if (!next) {
      setTargetDomain(backup.app_name);
      setTyped("");
      setRestoreEnv(true);
      restoreFromDestination.reset();
    }
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!matches) return;
    restoreFromDestination.mutate(
      {
        destination,
        backupId: backup.backup_id,
        appName: backup.app_name,
        targetDomain: targetDomain === backup.app_name ? undefined : targetDomain,
        restoreEnv,
      },
      { onSuccess: () => close(false) },
    );
  };

  return (
    <AlertDialog.Root open={open} onOpenChange={(next: boolean) => close(next)}>
      <AlertDialog.Portal>
        <AlertDialog.Backdrop className={BACKDROP} />
        <AlertDialog.Viewport className={MODAL_VIEWPORT}>
          <AlertDialog.Popup initialFocus={inputRef} className={cx(MODAL_POPUP, "sm:max-w-[480px]")}>
            <form onSubmit={submit} className="contents">
              <DialogFrame
                title={`Restore ${backup.backup_id}`}
                description={
                  targetDomain === backup.app_name
                    ? `Downloads this backup from ${destination} first, then replaces the files of the application ${backup.app_name} (and its database, if this backup includes one) with what it holds. Anything written since is lost.`
                    : `Downloads this backup from ${destination} first, then replaces the files of ${targetDomain || "the domain you type"} (and its database, if this backup includes one) with what it holds. Anything written since is lost.`
                }
                Title={AlertDialog.Title}
                Description={AlertDialog.Description}
                footer={
                  <>
                    <AlertDialog.Close render={<Button disabled={restoreFromDestination.isPending}>Cancel</Button>} />
                    <Button type="submit" variant="danger" disabled={!matches} loading={restoreFromDestination.isPending}>
                      Restore
                    </Button>
                  </>
                }
              >
                <div className="flex flex-col gap-4">
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="restore-remote-target-domain" className="text-13 font-medium text-fg">
                      Restore into
                    </label>
                    <Input
                      id="restore-remote-target-domain"
                      mono
                      value={targetDomain}
                      onValueChange={setTargetDomain}
                      autoComplete="off"
                      autoCapitalize="off"
                      spellCheck={false}
                      disabled={restoreFromDestination.isPending}
                    />
                  </div>
                  <Checkbox
                    checked={restoreEnv}
                    onCheckedChange={setRestoreEnv}
                    label="Restore .env files"
                    description="From the downloaded archive, replacing what is there now."
                  />
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="restore-remote-confirm" className="text-13 text-fg-muted">
                      Type{" "}
                      <span translate="no" className="mono rounded-[4px] bg-bg-sunken px-1 py-0.5 text-fg select-all">
                        {targetDomain || "the domain"}
                      </span>{" "}
                      to confirm
                    </label>
                    <Input
                      id="restore-remote-confirm"
                      ref={inputRef}
                      mono
                      value={typed}
                      onValueChange={setTyped}
                      autoComplete="off"
                      autoCapitalize="off"
                      spellCheck={false}
                      disabled={restoreFromDestination.isPending}
                    />
                  </div>
                  {restoreFromDestination.isError ? (
                    <ErrorBlock live compact error={restoreFromDestination.error} title="The restore did not start" />
                  ) : null}
                </div>
              </DialogFrame>
            </form>
          </AlertDialog.Popup>
        </AlertDialog.Viewport>
      </AlertDialog.Portal>
    </AlertDialog.Root>
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
}: {
  remote: ReturnType<typeof useQuery<RemoteBackups>>;
  name: string;
  app: string;
  columns: readonly Column<RemoteBackup>[];
  onSelectApp: (app: string) => void;
  onRestore: (backup: RemoteBackup) => void;
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
        title="Could not read that destination"
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
        caption={`Backups on ${name} for ${app}`}
        empty={<EmptyState title="No backups found" description="Nothing was found at that path." className="border-0 py-8" />}
        rowActions={(row) => (
          <Button size="sm" icon={<RotateCcw aria-hidden="true" />} onClick={() => onRestore(row)}>
            Restore
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
        title="Nothing found there"
        description="No application directories were found at this destination's path."
        className="border-0 py-8"
      />
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-13 text-fg-muted">Applications found at this destination. Choose one to see its backups.</p>
      <ul className="flex flex-col divide-y divide-border rounded-card border border-border">
        {apps.map((candidate) => (
          <li key={candidate}>
            <button
              type="button"
              onClick={() => onSelectApp(candidate)}
              className="flex w-full items-center px-3 py-2 text-left text-13 text-fg hover:bg-surface-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus"
            >
              <span translate="no" className="mono">
                {candidate}
              </span>
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

/** What a destination holds, browsed the way `wasm backup destination browse` does it. */
export function BrowseDestinationDialog({ destinations, initialDestination, open, onOpenChange }: BrowseDestinationDialogProps) {
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
    { id: "backup_id", header: "Backup", mono: true, cell: (row) => row.backup_id, sortValue: (row) => row.backup_id },
    {
      id: "modified",
      header: "Modified",
      width: "w-36",
      cell: (row) => <RelativeTime value={row.modified} />,
      sortValue: (row) => row.modified ?? "",
    },
    {
      id: "size",
      header: "Size",
      align: "end",
      mono: true,
      width: "w-24",
      cell: (row) => (row.size === null || row.size === undefined ? "-" : formatBytes(row.size)),
      sortValue: (row) => row.size ?? -1,
    },
  ];

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={close}
        size="lg"
        title="Browse a destination"
        description="What a remote destination holds, and restoring straight from it, without a local copy first."
        footer={<Button onClick={() => close(false)}>Close</Button>}
      >
        <div className="flex flex-col gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Destination" nativeLabel={false}>
              <Select
                aria-label="Destination"
                value={name}
                onValueChange={(next) => {
                  setName(next);
                  setApp("");
                }}
                options={destinations.map((destination) => ({ value: destination.name, label: destination.name }))}
                disabled={destinations.length === 0}
              />
            </Field>
            <Field label="Application" optional description="Leave blank to see every application found there.">
              <Input mono value={app} onValueChange={setApp} placeholder="shop-example-com" autoComplete="off" spellCheck={false} />
            </Field>
          </div>

          {name === "" ? (
            <EmptyState icon={<FolderOpen />} title="No destinations yet" description="Add one first." className="border-0 py-8" />
          ) : (
            <DestinationBrowseResult
              remote={remote}
              name={name}
              app={app}
              columns={columns}
              onSelectApp={setApp}
              onRestore={setRestoring}
            />
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
