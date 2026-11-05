import { useQuery } from "@tanstack/react-query";
import { KeyRound, Plus, RotateCw } from "lucide-react";
import { useState } from "react";

import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { RowMenu } from "../../components/page/RowMenu";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { useCan } from "../../components/page/permissions";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { FirstUse } from "../../components/ui/FirstUse";
import { ICONS } from "../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { AccountDialog } from "./AccountDialog";
import { cloudAccountsQuery, cloudMachinesQuery } from "./data";
import type { CloudAccount, CloudMachine } from "./data";
import { providerName } from "./providerName";
import { useCloudActions } from "./useCloudActions";

function columnsFor(t: T, machines: readonly CloudMachine[] | undefined): readonly Column<CloudAccount>[] {
  return [
    {
      id: "name",
      header: t("cloud.accounts.column.name"),
      sortValue: (account) => account.name,
      // The account's note is what the operator wrote to tell two apart: it is the account's title, on the row.
      cell: (account) => <Mono className="font-medium">{account.name}</Mono>,
    },
    { id: "provider", header: t("cloud.accounts.column.provider"), cell: (account) => <span className="text-13 text-fg">{providerName(t, account.provider)}</span> },
    {
      id: "servers",
      header: t("cloud.accounts.column.servers"),
      align: "end",
      hideBelow: "sm",
      // A machine that was deleted at the provider is no longer a server of the account.
      cell: (account) => (machines === undefined ? null : <span className="text-13 text-fg tabular-nums">{machines.filter((machine) => machine.account === account.name && machine.state !== "deleted").length}</span>),
    },
    {
      id: "token",
      header: t("cloud.accounts.column.token"),
      cell: (account) => (
        <span className="flex min-w-0 items-center gap-3">
          <Mono tone="muted">{account.token_hint}</Mono>
          <RelativeTime value={account.verified_at} fallback={t("cloud.accounts.notChecked")} className="text-13 text-fg-muted" />
        </span>
      ),
    },
    {
      id: "note",
      header: t("cloud.accounts.column.note"),
      hideBelow: "lg",
      cell: (account) => (account.note !== null && account.note !== undefined && account.note !== "" ? <span className="text-13 text-fg-muted">{account.note}</span> : null),
    },
  ];
}

function RemoveAccountDialog({ account, open, onOpenChange }: { account: CloudAccount; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const { removeAccount } = useCloudActions();
  const [force, setForce] = useState(false);
  return (
    <ConfirmDialog
      friction="simple"
      destructive
      open={open}
      onOpenChange={(next) => {
        if (!next) setForce(false);
        onOpenChange(next);
      }}
      title={t("cloud.accounts.removeTitle", { name: account.name })}
      description={force ? t("cloud.accounts.removeForce") : t("cloud.accounts.removeBody")}
      actionLabel={force ? t("cloud.accounts.removeForceAction") : t("cloud.accounts.removeAction")}
      onConfirm={async () => {
        try {
          // design-exception: write-without-permission opened only from the account's row menu, which is drawn only for fleet.manage
          await removeAccount.mutateAsync({ name: account.name, force });
        } catch (error: unknown) {
          // Servers made with it are still on record: the second attempt offers to go on anyway.
          if (!force && error instanceof Error && "detail" in error && String((error as { detail: unknown }).detail).includes("still has servers")) setForce(true);
          throw error;
        }
      }}
    />
  );
}

function AccountActions({ account }: { account: CloudAccount }) {
  const t = useT();
  const { verifyAccount } = useCloudActions();
  const [replacing, setReplacing] = useState(false);
  const [removing, setRemoving] = useState(false);
  return (
    <>
      <RowMenu name={account.name}>
        <MenuItem icon={<RotateCw />} disabled={verifyAccount.isPending} onClick={() => verifyAccount.mutate(account.name)}>
          {t("cloud.accounts.verify")}
        </MenuItem>
        <MenuItem icon={<KeyRound />} onClick={() => setReplacing(true)}>
          {t("cloud.accounts.replaceToken")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem icon={<ICONS.delete />} destructive onClick={() => setRemoving(true)}>
          {t("common.words.remove")}
        </MenuItem>
      </RowMenu>
      {replacing ? <AccountDialog existing={account} open onOpenChange={setReplacing} /> : null}
      <RemoveAccountDialog account={account} open={removing} onOpenChange={setRemoving} />
    </>
  );
}

/**
 * The provider accounts this central creates servers with, and the one place their tokens are given: a slab with its
 * own head, the table inside it, and, the first time, one sentence that says what an account gives and the button
 * that adds it. A role that cannot manage the fleet sees the rows and neither the button nor the row menus.
 */
export function AccountsSection({ adding, onAddingChange }: { adding: boolean; onAddingChange: (adding: boolean) => void }) {
  const t = useT();
  const canManage = useCan("fleet.manage");
  const query = useQuery(cloudAccountsQuery());
  const machines = useQuery(cloudMachinesQuery());
  const columns = columnsFor(t, machines.data);
  const hasRows = query.data !== undefined && query.data.length > 0;
  const add = (
    <Button
      variant={hasRows ? "secondary" : "primary"}
      size={hasRows ? "sm" : "md"}
      icon={<Plus aria-hidden="true" />}
      onClick={() => {
        onAddingChange(true);
      }}
    >
      {t("cloud.accounts.add")}
    </Button>
  );
  return (
    <SurfaceSection
      title={t("cloud.accounts.title")}
      icon={OBJECT_ICONS.cloud}
      description={t("cloud.accounts.description")}
      actions={hasRows && canManage ? add : undefined}
      flush={query.data === undefined || hasRows}
    >
      <QueryState
        query={query}
        label={t("cloud.accounts.loadingLabel")}
        skeleton={<DataTable embedded caption={t("cloud.accounts.caption")} columns={columns} rows={[]} getRowId={(account) => account.name} loading skeletonRows={2} />}
        isEmpty={(data) => data.length === 0}
        empty={<FirstUse {...(canManage ? { action: add } : {})}>{t("cloud.accounts.empty.sentence")}</FirstUse>}
      >
        {(accounts) => (
          <DataTable
            embedded
            caption={t("cloud.accounts.caption")}
            columns={columns}
            rows={[...accounts].sort((a, b) => a.name.localeCompare(b.name))}
            getRowId={(account) => account.name}
            {...(canManage ? { rowActions: (account: CloudAccount) => <AccountActions account={account} /> } : {})}
          />
        )}
      </QueryState>
      {adding && canManage ? <AccountDialog open onOpenChange={onAddingChange} /> : null}
    </SurfaceSection>
  );
}
