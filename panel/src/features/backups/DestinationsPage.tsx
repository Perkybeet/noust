import { useQuery } from "@tanstack/react-query";
import { Cloud, FolderOpen, KeyRound, Lock, Pencil, Wifi } from "lucide-react";
import { useState } from "react";

import { isApiError } from "../../api/client";
import type { Destination } from "../../api/queries/backupDestinations";
import { backupDestinationsQuery } from "../../api/queries/backupDestinations";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { StatusPill } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { describeError } from "../../lib/errors";
import { backendLabel } from "./backendCatalog";
import { BackupsListPage } from "./BackupsListPage";
import { BrowseDestinationDialog } from "./BrowseDestinationDialog";
import { DestinationDialog } from "./DestinationDialog";
import { ShowKeyDialog } from "./ShowKeyDialog";
import { useDestinationActions } from "./useDestinationActions";

interface TestState {
  checking: boolean;
  ok?: boolean;
}

/** A destination's reachability, from a test run this session: the state of the connection. */
function ConnectionCell({ state, t }: { state: TestState | undefined; t: T }) {
  if (state?.checking === true) return <StatusPill state="deploying" label={t("backups.destinations.test.testing")} appearance="inline" size="sm" />;
  if (state?.ok === true) return <StatusPill state="running" label={t("backups.destinations.test.reachable")} appearance="inline" size="sm" />;
  if (state?.ok === false) return <StatusPill state="failed" label={t("backups.destinations.test.unreachable")} appearance="inline" size="sm" />;
  return <StatusPill state="unknown" label={t("backups.destinations.test.notTested")} appearance="inline" size="sm" />;
}

/**
 * Removing a destination: one question (friction "simple", docs/DESIGN.md 6.2): what is
 * already there stays there. A destination a schedule still copies to is refused with a hint to
 * pass `--force`; the second attempt offers exactly that, instead of asking the operator to
 * find a CLI flag. An encrypted one only gets here after `ShowKeyDialog` showed its key and the
 * operator ticked "saved" (`keySaved`), which is what the API requires to remove it.
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
      friction="simple"
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
  // Removing deletes the only copy Noust has of an encrypted destination's key.
  const keyed = destination.encrypted && destination.encryption_configured;

  return (
    <>
      <Menu align="end" trigger={<IconButton label={t("backups.destinations.actionsFor", { name: destination.name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
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
        <MenuSeparator />
        <MenuItem icon={<ICONS.delete />} destructive onClick={() => (keyed ? setRemoveKeyOpen(true) : setRemoveOpen(true))}>
          {t("backups.destinations.remove")}
        </MenuItem>
      </Menu>
      {editOpen ? <DestinationDialog existing={destination} open onOpenChange={setEditOpen} /> : null}
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
 * The Destinations tab: the other places each backup can be copied to (an SFTP server, S3
 * storage, a cloud drive), whether they answer, and whether what is sent there is encrypted.
 * `useBackupRefresh` covers the backups themselves; a destination's list refreshes only from
 * its own actions, since nothing else on the server changes it.
 */
export function DestinationsPage() {
  const t = useT();
  const destinations = useQuery(backupDestinationsQuery());
  const { test } = useDestinationActions();
  const [addOpen, setAddOpen] = useState(false);
  const [browse, setBrowse] = useState<{ target: string | undefined } | null>(null);
  const [testStates, setTestStates] = useState<Record<string, TestState>>({});
  const list = destinations.data?.destinations ?? [];
  const empty = destinations.data !== undefined && list.length === 0;

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
          // rclone's own words, verbatim, when it printed any.
          ...(described.output !== null ? { output: described.output } : {}),
        });
      },
    });
  };

  const columns: Column<Destination>[] = [
    { id: "name", header: t("backups.destinations.columns.name"), mono: true, cell: (row) => row.name, sortValue: (row) => row.name },
    {
      id: "connection",
      header: t("backups.destinations.columns.connection"),
      width: "w-36",
      card: "status",
      cell: (row) => <ConnectionCell state={testStates[row.name]} t={t} />,
    },
    {
      id: "backend",
      header: t("backups.destinations.columns.backend"),
      cell: (row) => backendLabel(row.backend),
      sortValue: (row) => backendLabel(row.backend),
    },
    {
      id: "encrypted",
      header: t("backups.destinations.columns.encryption"),
      hideBelow: "sm",
      cell: (row) =>
        row.encrypted ? (
          <span className="inline-flex items-center gap-1.5 text-fg">
            <Lock aria-hidden="true" className="size-icon-sm shrink-0 text-fg-muted" />
            {t("backups.destinations.encrypted")}
          </span>
        ) : (
          <span className="text-fg-muted">{t("backups.destinations.notEncrypted")}</span>
        ),
    },
    {
      id: "path",
      header: t("backups.destinations.columns.path"),
      hideBelow: "md",
      card: "hidden",
      mono: true,
      cell: (row) => row.settings["path"] ?? "/",
    },
    {
      id: "updated",
      header: t("backups.destinations.columns.updated"),
      hideBelow: "lg",
      card: "hidden",
      width: "w-32",
      cell: (row) => <RelativeTime value={row.updated_at} />,
      sortValue: (row) => row.updated_at ?? "",
    },
  ];

  const addButton = (variant: "primary" | "secondary") => (
    <Button variant={variant} icon={<ICONS.add aria-hidden="true" />} onClick={() => setAddOpen(true)}>
      {t("backups.destinations.add")}
    </Button>
  );

  let content;
  if (destinations.isError && destinations.data === undefined) {
    content = (
      <ErrorBlock error={destinations.error} title={t("backups.destinations.loadError")} onRetry={() => void destinations.refetch()} retrying={destinations.isRefetching} />
    );
  } else if (empty) {
    content = (
      <EmptyState
        variant="firstUse"
        icon={<Cloud />}
        title={t("backups.destinations.empty.title")}
        description={t("backups.destinations.empty.description")}
        action={addButton("secondary")}
        command="noust backup destination add <name> --type <backend>"
      />
    );
  } else {
    content = (
      <DataTable
        mobile="cards"
        columns={columns}
        rows={list}
        getRowId={(row) => row.name}
        caption={t("backups.destinations.caption")}
        loading={destinations.isPending}
        skeletonRows={2}
        rowActions={(row) => (
          <DestinationActions destination={row} testState={testStates[row.name]} onTest={() => runTest(row.name)} onBrowse={() => setBrowse({ target: row.name })} />
        )}
        defaultSort={{ column: "name", direction: "ascending" }}
      />
    );
  }

  return (
    <BackupsListPage
      {...(list.length > 0
        ? {
            secondaryActions: (
              <Button icon={<FolderOpen aria-hidden="true" />} onClick={() => setBrowse({ target: undefined })}>
                {t("backups.destinations.browse")}
              </Button>
            ),
          }
        : {})}
      primaryAction={addButton("primary")}
      {...(empty ? {} : { footer: <CommandHint command="noust backup destination list" label={t("backups.common.fromTerminal")} /> })}
    >
      {content}
      {addOpen ? <DestinationDialog open onOpenChange={setAddOpen} /> : null}
      {/* Keyed by which destination opened it, so browsing a different row starts fresh
          instead of keeping the previous one's selected destination and app filter. */}
      {browse !== null ? (
        <BrowseDestinationDialog
          key={browse.target ?? "*"}
          destinations={list}
          {...(browse.target !== undefined ? { initialDestination: browse.target } : {})}
          open
          onOpenChange={(next) => !next && setBrowse(null)}
        />
      ) : null}
    </BackupsListPage>
  );
}
