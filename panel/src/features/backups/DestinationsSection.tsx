import { useQuery } from "@tanstack/react-query";
import { Cloud, FolderOpen, KeyRound, Lock, MoreHorizontal, Pencil, Plus, ShieldOff, Trash2, Wifi } from "lucide-react";
import { useState } from "react";

import { isApiError } from "../../api/client";
import type { Destination } from "../../api/queries/backupDestinations";
import { backupDestinationsQuery } from "../../api/queries/backupDestinations";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { describeError } from "../../lib/errors";
import { backendLabel } from "./backendCatalog";
import { BrowseDestinationDialog } from "./BrowseDestinationDialog";
import { DestinationDialog } from "./DestinationDialog";
import { ShowKeyDialog } from "./ShowKeyDialog";
import { useDestinationActions } from "./useDestinationActions";

interface TestState {
  checking: boolean;
  ok?: boolean;
}

/** One row's "Reachable" verdict, from a test run this session; nothing until one runs. */
function TestCell({ state }: { state: TestState | undefined }) {
  if (state === undefined) return <span className="text-13 text-fg-faint">Not tested</span>;
  if (state.checking) return <StatusPill state="deploying" label="Testing" appearance="inline" size="sm" />;
  if (state.ok === undefined) return <span className="text-13 text-fg-faint">Not tested</span>;
  return state.ok ? (
    <StatusPill state="running" label="Reachable" appearance="inline" size="sm" />
  ) : (
    <StatusPill state="failed" label="Unreachable" appearance="inline" size="sm" />
  );
}

/**
 * Removing a destination: the same type-to-confirm `ConfirmDialog` every irreversible action
 * uses. A destination a schedule still pushes to is refused with a hint to pass `--force`; the
 * second attempt offers exactly that, instead of asking the operator to find a CLI flag.
 */
function RemoveDestinationDialog({ destination, open, onOpenChange }: { destination: Destination; open: boolean; onOpenChange: (open: boolean) => void }) {
  const { remove } = useDestinationActions();
  const [force, setForce] = useState(false);

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setForce(false);
        onOpenChange(next);
      }}
      title={`Remove ${destination.name}`}
      description={
        force
          ? "A schedule still pushes backups here. Removing it anyway drops the reference from those schedules; backups already copied here are kept. Type the name again to remove it."
          : "Backups already copied here are kept, but nothing more will be sent to it. Refused while a schedule still pushes to it."
      }
      confirmText={destination.name}
      actionLabel={force ? "Remove anyway" : "Remove destination"}
      onConfirm={async () => {
        try {
          await remove.mutateAsync({ name: destination.name, force });
        } catch (error: unknown) {
          if (!force && isApiError(error) && (error.hint ?? "").toLowerCase().includes("force")) {
            setForce(true);
          }
          throw error;
        }
      }}
    />
  );
}

function DestinationActions({
  destination,
  testState,
  onTest,
  onBrowse,
}: {
  destination: Destination;
  testState: TestState | undefined;
  onTest: () => void;
  onBrowse: () => void;
}) {
  const [editOpen, setEditOpen] = useState(false);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);

  return (
    <>
      <Menu align="end" trigger={<IconButton label={`Actions for ${destination.name}`} icon={<MoreHorizontal />} size="sm" tooltip={false} />}>
        <MenuItem icon={<Wifi />} disabled={testState?.checking === true} onClick={onTest}>
          Test
        </MenuItem>
        <MenuItem icon={<FolderOpen />} onClick={onBrowse}>
          Browse
        </MenuItem>
        <MenuItem icon={<Pencil />} onClick={() => setEditOpen(true)}>
          Edit
        </MenuItem>
        {destination.encrypted ? (
          <MenuItem icon={<KeyRound />} onClick={() => setKeyOpen(true)}>
            Show encryption key
          </MenuItem>
        ) : null}
        <MenuItem icon={<Trash2 />} destructive onClick={() => setRemoveOpen(true)}>
          Remove
        </MenuItem>
      </Menu>
      <DestinationDialog existing={destination} open={editOpen} onOpenChange={setEditOpen} />
      <RemoveDestinationDialog destination={destination} open={removeOpen} onOpenChange={setRemoveOpen} />
      {keyOpen ? <ShowKeyDialog name={destination.name} open onOpenChange={setKeyOpen} /> : null}
    </>
  );
}

/**
 * Remote places backups can be pushed to or restored from, over rclone. `useBackupRefresh`
 * covers the backups list itself; a destination's list refreshes only from its own actions,
 * since nothing else on the server changes it.
 */
export function DestinationsSection() {
  const destinations = useQuery(backupDestinationsQuery());
  const { test } = useDestinationActions();
  const [addOpen, setAddOpen] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [browseTarget, setBrowseTarget] = useState<string | undefined>(undefined);
  const [testStates, setTestStates] = useState<Record<string, TestState>>({});

  const runTest = (name: string): void => {
    setTestStates((current) => ({ ...current, [name]: { checking: true } }));
    test.mutate(name, {
      onSuccess: (result) => {
        setTestStates((current) => ({ ...current, [name]: { checking: false, ok: result.ok } }));
        const entries = result.entries?.join(", ") ?? "";
        if (result.ok) toast.success(`${name} is reachable`, entries !== "" ? { detail: entries } : {});
        else toast.error(`${name} could not be reached`);
      },
      onError: (error) => {
        setTestStates((current) => ({ ...current, [name]: { checking: false, ok: false } }));
        const described = describeError(error);
        toast.error(`${name} could not be reached`, { detail: described.detail, ...(described.hint !== null ? { description: described.hint } : {}) });
      },
    });
  };

  const openBrowse = (name?: string): void => {
    setBrowseTarget(name);
    setBrowseOpen(true);
  };

  const columns: Column<Destination>[] = [
    { id: "name", header: "Name", mono: true, cell: (row) => row.name, sortValue: (row) => row.name },
    { id: "backend", header: "Backend", cell: (row) => backendLabel(row.backend), sortValue: (row) => backendLabel(row.backend) },
    {
      id: "encrypted",
      header: "Encrypted",
      cell: (row) =>
        row.encrypted ? (
          <span className="flex items-center gap-1.5 text-13 text-fg">
            <Lock aria-hidden="true" className="size-3.5 shrink-0 text-fg-muted" />
            Encrypted
          </span>
        ) : (
          <span className="flex items-center gap-1.5 text-13 text-fg-faint">
            <ShieldOff aria-hidden="true" className="size-3.5 shrink-0" />
            Not encrypted
          </span>
        ),
    },
    {
      id: "path",
      header: "Path",
      hideBelow: "md",
      mono: true,
      cell: (row) => row.settings["path"] ?? "/",
    },
    {
      id: "test",
      header: "Last test",
      hideBelow: "sm",
      cell: (row) => <TestCell state={testStates[row.name]} />,
    },
    {
      id: "updated",
      header: "Updated",
      hideBelow: "lg",
      cell: (row) => <RelativeTime value={row.updated_at} />,
      sortValue: (row) => row.updated_at ?? "",
    },
  ];

  return (
    <Section
      title="Destinations"
      description="Remote places a backup can be copied to, by hand or on a schedule, over rclone."
      actions={
        destinations.data !== undefined && destinations.data.destinations.length > 0 ? (
          <>
            <Button size="sm" icon={<FolderOpen aria-hidden="true" />} onClick={() => openBrowse(undefined)}>
              Browse
            </Button>
            <Button size="sm" icon={<Plus aria-hidden="true" />} onClick={() => setAddOpen(true)}>
              Add destination
            </Button>
          </>
        ) : undefined
      }
    >
      <QueryState
        query={destinations}
        label="backup destinations"
        skeleton={
          <div aria-hidden="true" className="flex flex-col gap-2">
            {[0, 1].map((i) => (
              <Skeleton key={i} className="h-10 rounded-card" />
            ))}
          </div>
        }
        isEmpty={(data) => data.destinations.length === 0}
        empty={
          <EmptyState
            icon={<Cloud />}
            title="No destinations yet"
            description="Add one to copy backups off this machine: an SFTP server, S3-compatible storage, Backblaze B2, or a cloud drive."
            action={
              <Button icon={<Plus aria-hidden="true" />} onClick={() => setAddOpen(true)}>
                Add destination
              </Button>
            }
            command="wasm backup destination add <name> --type <backend>"
          />
        }
      >
        {(data) => (
          <DataTable
            columns={columns}
            rows={data.destinations}
            getRowId={(row) => row.name}
            caption="Backup destinations"
            rowActions={(row) => (
              <DestinationActions
                destination={row}
                testState={testStates[row.name]}
                onTest={() => runTest(row.name)}
                onBrowse={() => openBrowse(row.name)}
              />
            )}
            defaultSort={{ column: "name", direction: "ascending" }}
          />
        )}
      </QueryState>
      <DestinationDialog open={addOpen} onOpenChange={setAddOpen} />
      {/* Keyed by which destination opened it, so browsing a different row starts fresh
          instead of keeping the previous one's selected destination and app filter. */}
      {browseOpen ? (
        <BrowseDestinationDialog
          key={browseTarget ?? "*"}
          destinations={destinations.data?.destinations ?? []}
          {...(browseTarget !== undefined ? { initialDestination: browseTarget } : {})}
          open
          onOpenChange={setBrowseOpen}
        />
      ) : null}
    </Section>
  );
}
