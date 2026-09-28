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
import { useT } from "../../i18n";
import type { T } from "../../i18n";
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
function TestCell({ state, t }: { state: TestState | undefined; t: T }) {
  if (state === undefined) return <span className="text-13 text-fg-faint">{t("backups.destinations.test.notTested")}</span>;
  if (state.checking) return <StatusPill state="deploying" label={t("backups.destinations.test.testing")} appearance="inline" size="sm" />;
  if (state.ok === undefined) return <span className="text-13 text-fg-faint">{t("backups.destinations.test.notTested")}</span>;
  return state.ok ? (
    <StatusPill state="running" label={t("backups.destinations.test.reachable")} appearance="inline" size="sm" />
  ) : (
    <StatusPill state="failed" label={t("backups.destinations.test.unreachable")} appearance="inline" size="sm" />
  );
}

/**
 * Removing a destination: the same type-to-confirm `ConfirmDialog` every irreversible action
 * uses. A destination a schedule still pushes to is refused with a hint to pass `--force`; the
 * second attempt offers exactly that, instead of asking the operator to find a CLI flag. An
 * encrypted one only gets here after `ShowKeyDialog` showed its key and the operator ticked
 * "saved" (`keySaved`), which is what the API requires to remove it.
 */
function RemoveDestinationDialog({
  destination,
  open,
  onOpenChange,
  keySaved,
}: {
  destination: Destination;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  keySaved: boolean;
}) {
  const t = useT();
  const { remove } = useDestinationActions();
  const [force, setForce] = useState(false);
  const leftBehind = keySaved ? t("backups.destinations.removeDialog.leftBehindKeyed") : t("backups.destinations.removeDialog.leftBehind");

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setForce(false);
        onOpenChange(next);
      }}
      title={t("backups.destinations.removeDialog.title", { name: destination.name })}
      description={
        force
          ? t("backups.destinations.removeDialog.descriptionForce", { leftBehind })
          : t("backups.destinations.removeDialog.descriptionRefused", { leftBehind })
      }
      confirmText={destination.name}
      actionLabel={force ? t("backups.destinations.removeDialog.actionForce") : t("backups.destinations.removeDialog.actionDefault")}
      onConfirm={async () => {
        try {
          await remove.mutateAsync({ name: destination.name, force, keySaved });
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
  const t = useT();
  const [editOpen, setEditOpen] = useState(false);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const [removeKeyOpen, setRemoveKeyOpen] = useState(false);
  // Removing deletes the only copy WASM has of an encrypted destination's key.
  const keyed = destination.encrypted && destination.encryption_configured;

  return (
    <>
      <Menu
        align="end"
        trigger={<IconButton label={t("backups.destinations.actionsFor", { name: destination.name })} icon={<MoreHorizontal />} size="sm" tooltip={false} />}
      >
        <MenuItem icon={<Wifi />} disabled={testState?.checking === true} onClick={onTest}>
          {t("backups.destinations.test.label")}
        </MenuItem>
        <MenuItem icon={<FolderOpen />} onClick={onBrowse}>
          {t("backups.destinations.browse")}
        </MenuItem>
        <MenuItem icon={<Pencil />} onClick={() => setEditOpen(true)}>
          {t("backups.common.edit")}
        </MenuItem>
        {destination.encrypted ? (
          <MenuItem icon={<KeyRound />} onClick={() => setKeyOpen(true)}>
            {t("backups.destinations.showKey")}
          </MenuItem>
        ) : null}
        <MenuItem icon={<Trash2 />} destructive onClick={() => (keyed ? setRemoveKeyOpen(true) : setRemoveOpen(true))}>
          {t("backups.destinations.remove")}
        </MenuItem>
      </Menu>
      <DestinationDialog existing={destination} open={editOpen} onOpenChange={setEditOpen} />
      <RemoveDestinationDialog destination={destination} open={removeOpen} onOpenChange={setRemoveOpen} keySaved={keyed} />
      {keyOpen ? <ShowKeyDialog name={destination.name} open onOpenChange={setKeyOpen} /> : null}
      {removeKeyOpen ? (
        <ShowKeyDialog
          name={destination.name}
          open
          onOpenChange={setRemoveKeyOpen}
          onContinueToRemove={() => {
            setRemoveOpen(true);
          }}
        />
      ) : null}
    </>
  );
}

/**
 * Remote places backups can be pushed to or restored from, over rclone. `useBackupRefresh`
 * covers the backups list itself; a destination's list refreshes only from its own actions,
 * since nothing else on the server changes it.
 */
export function DestinationsSection() {
  const t = useT();
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
        if (result.ok) toast.success(t("backups.destinations.toast.reachable", { name }), entries !== "" ? { detail: entries } : {});
        else toast.error(t("backups.destinations.toast.unreachable", { name }));
      },
      onError: (error) => {
        setTestStates((current) => ({ ...current, [name]: { checking: false, ok: false } }));
        const described = describeError(error);
        toast.error(t("backups.destinations.toast.unreachable", { name }), {
          detail: described.detail,
          ...(described.hint !== null ? { description: described.hint } : {}),
        });
      },
    });
  };

  const openBrowse = (name?: string): void => {
    setBrowseTarget(name);
    setBrowseOpen(true);
  };

  const columns: Column<Destination>[] = [
    { id: "name", header: t("backups.destinations.columns.name"), mono: true, cell: (row) => row.name, sortValue: (row) => row.name },
    {
      id: "backend",
      header: t("backups.destinations.columns.backend"),
      cell: (row) => backendLabel(row.backend),
      sortValue: (row) => backendLabel(row.backend),
    },
    {
      id: "encrypted",
      header: t("backups.destinations.encrypted"),
      cell: (row) =>
        row.encrypted ? (
          <span className="flex items-center gap-1.5 text-13 text-fg">
            <Lock aria-hidden="true" className="size-3.5 shrink-0 text-fg-muted" />
            {t("backups.destinations.encrypted")}
          </span>
        ) : (
          <span className="flex items-center gap-1.5 text-13 text-fg-faint">
            <ShieldOff aria-hidden="true" className="size-3.5 shrink-0" />
            {t("backups.destinations.notEncrypted")}
          </span>
        ),
    },
    {
      id: "path",
      header: t("backups.destinations.columns.path"),
      hideBelow: "md",
      mono: true,
      cell: (row) => row.settings["path"] ?? "/",
    },
    {
      id: "test",
      header: t("backups.destinations.columns.lastTest"),
      hideBelow: "sm",
      cell: (row) => <TestCell state={testStates[row.name]} t={t} />,
    },
    {
      id: "updated",
      header: t("backups.destinations.columns.updated"),
      hideBelow: "lg",
      cell: (row) => <RelativeTime value={row.updated_at} />,
      sortValue: (row) => row.updated_at ?? "",
    },
  ];

  return (
    <Section
      title={t("backups.destinations.sectionTitle")}
      description={t("backups.destinations.sectionDescription")}
      actions={
        destinations.data !== undefined && destinations.data.destinations.length > 0 ? (
          <>
            <Button size="sm" icon={<FolderOpen aria-hidden="true" />} onClick={() => openBrowse(undefined)}>
              {t("backups.destinations.browse")}
            </Button>
            <Button size="sm" icon={<Plus aria-hidden="true" />} onClick={() => setAddOpen(true)}>
              {t("backups.destinations.add")}
            </Button>
          </>
        ) : undefined
      }
    >
      <QueryState
        query={destinations}
        label={t("backups.destinations.queryLabel")}
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
            title={t("backups.destinations.empty.title")}
            description={t("backups.destinations.empty.description")}
            action={
              <Button icon={<Plus aria-hidden="true" />} onClick={() => setAddOpen(true)}>
                {t("backups.destinations.add")}
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
            caption={t("backups.destinations.caption")}
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
