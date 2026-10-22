import { useQuery } from "@tanstack/react-query";
import { KeyRound, Plus, RotateCw } from "lucide-react";
import { useState } from "react";

import { CommandHint } from "../../components/page/CommandHint";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { RowMenu } from "../../components/page/RowMenu";
import { ICONS } from "../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { AccountDialog } from "./AccountDialog";
import { cloudAccountsQuery } from "./data";
import type { CloudAccount, ProviderKind } from "./data";
import { useCloudActions } from "./useCloudActions";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

function providerName(t: T, provider: string): string {
  return provider === "hetzner" || provider === "digitalocean" || provider === "vultr" ? t(`cloud.provider.${provider satisfies ProviderKind}.name`) : provider;
}

function columnsFor(t: T): readonly Column<CloudAccount>[] {
  return [
    { id: "name", header: t("cloud.accounts.column.name"), sortValue: (account) => account.name, cell: (account) => <Mono className="font-medium">{account.name}</Mono> },
    { id: "provider", header: t("cloud.accounts.column.provider"), cell: (account) => <span className="text-13 text-fg">{providerName(t, account.provider)}</span> },
    { id: "token", header: t("cloud.accounts.column.token"), hideBelow: "sm", cell: (account) => <Mono tone="muted">{account.token_hint}</Mono> },
    {
      id: "verified",
      header: t("cloud.accounts.column.verified"),
      hideBelow: "md",
      cell: (account) => <RelativeTime value={account.verified_at} fallback={t("cloud.accounts.never")} className="text-13 text-fg-muted" />,
    },
    {
      id: "used",
      header: t("cloud.accounts.column.lastUsed"),
      hideBelow: "lg",
      cell: (account) => <RelativeTime value={account.last_used_at} fallback={t("cloud.accounts.never")} className="text-13 text-fg-muted" />,
    },
    {
      id: "note",
      header: t("cloud.accounts.column.note"),
      hideBelow: "lg",
      cell: (account) => (account.note !== null && account.note !== undefined && account.note !== "" ? <span className="text-13 text-fg-muted">{account.note}</span> : <EmptyCell reason={t("cloud.accounts.noNote")} />),
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
          {t("cloud.accounts.remove")}
        </MenuItem>
      </RowMenu>
      {replacing ? <AccountDialog existing={account} open onOpenChange={setReplacing} /> : null}
      <RemoveAccountDialog account={account} open={removing} onOpenChange={setRemoving} />
    </>
  );
}

/** The provider accounts this central creates servers with, and the one place their tokens are given. */
export function AccountsSection({ adding, onAddingChange }: { adding: boolean; onAddingChange: (adding: boolean) => void }) {
  const t = useT();
  const query = useQuery(cloudAccountsQuery());
  const columns = columnsFor(t);
  const add = (
    <Button
      variant="primary"
      icon={<Plus aria-hidden="true" />}
      onClick={() => {
        onAddingChange(true);
      }}
    >
      {t("cloud.accounts.add")}
    </Button>
  );
  return (
    <Section title={t("cloud.accounts.title")} icon={OBJECT_ICONS.cloud} description={t("cloud.accounts.description")} actions={query.data !== undefined && query.data.length > 0 ? add : undefined}>
      <QueryState
        query={query}
        label={t("cloud.accounts.loadingLabel")}
        skeleton={<DataTable caption={t("cloud.accounts.caption")} columns={columns} rows={[]} getRowId={(account) => account.name} loading skeletonRows={2} />}
        isEmpty={(data) => data.length === 0}
        empty={<EmptyState variant="inline" title={t("cloud.accounts.empty.title")} description={t("cloud.accounts.empty.description")} action={add} />}
      >
        {(accounts) => <DataTable caption={t("cloud.accounts.caption")} columns={columns} rows={[...accounts].sort((a, b) => a.name.localeCompare(b.name))} getRowId={(account) => account.name} rowActions={(account) => <AccountActions account={account} />} />}
      </QueryState>
      <CommandHint label={t("cloud.fromTerminal")} command="noust fleet cloud account list" />
      {adding ? <AccountDialog open onOpenChange={onAddingChange} /> : null}
    </Section>
  );
}
