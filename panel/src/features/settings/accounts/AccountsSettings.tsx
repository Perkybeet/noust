import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { LockOpen, MailPlus, ShieldOff, UserCheck, UserCog, UserPlus, UserX, Users } from "lucide-react";
import { useMemo, useState } from "react";

import { sessionQuery } from "../../../api/queries/auth";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { FilterBar } from "../../../components/page/FilterBar";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section, Sections } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { StatusGlyph, stateTextClass } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { AccessReviewSection } from "./AccessReview";
import { DisableDialog, ExceptionDialog, RoleDialog } from "./AccountDialogs";
import { accountKeys, accountsQuery, enableAccount, exceptionsQuery, removeAccount, resetMfa, revokeException, unlockAccount } from "./api";
import type { Account, SeparationConflict, SodException } from "./api";
import { accountStatus, factorLabel, filterAccounts, isFiltered, stateView } from "./data";
import type { AccountStatus, AccountsSearch } from "./data";
import { InviteDialog } from "./InviteDialog";
import { ROLES, roleLabel } from "./roles";

const ALL = "all";
const STATE_FILTERS: readonly AccountStatus[] = ["active", "locked", "disabled", "invited"];

/** An account's state: colour, shape and word - except "Active", which is not a running state. */
export function AccountState({ t, account }: { t: T; account: Account }) {
  const view = stateView(t, account);
  if (view.state === null) {
    return (
      <span className="inline-flex items-center gap-1.5 text-13 text-fg">
        <UserCheck aria-hidden="true" className="size-icon-sm text-fg-muted" />
        {view.label}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-13">
      <StatusGlyph state={view.state} className={stateTextClass(view.state)} />
      <span className={view.state === "stopped" ? "text-fg-muted" : "text-fg"}>{view.label}</span>
    </span>
  );
}

/** A missing second factor is a problem to see at a glance; one that exists is plain text. */
function Factor({ t, account }: { t: T; account: Account }) {
  if (account.mfa_enabled) return <span className="text-13 text-fg">{factorLabel(t, account)}</span>;
  return (
    <span className="inline-flex items-center gap-1.5 text-13 text-fg">
      <StatusGlyph state="warning" className={stateTextClass("warning")} />
      {factorLabel(t, account)}
    </span>
  );
}

function columnsFor(t: T): Column<Account>[] {
  return [
    {
      id: "account",
      header: t("accounts.table.account"),
      card: "title",
      sortValue: (account) => account.username,
      cell: (account) => (
        <span className="flex min-w-0 flex-col">
          <Mono tone="default" truncate>
            {account.username}
          </Mono>
          {account.display_name !== "" ? <span className="truncate text-12 text-fg-muted">{account.display_name}</span> : null}
        </span>
      ),
    },
    { id: "state", header: t("accounts.table.state"), card: "status", width: "w-40", cell: (account) => <AccountState t={t} account={account} /> },
    { id: "role", header: t("accounts.table.role"), sortValue: (account) => account.role, cell: (account) => <Badge>{roleLabel(t, account.role)}</Badge> },
    { id: "factor", header: t("accounts.table.factor"), hideBelow: "md", cell: (account) => <Factor t={t} account={account} /> },
    {
      id: "last",
      header: t("accounts.table.lastSignIn"),
      hideBelow: "sm",
      sortValue: (account) => account.last_login_at ?? 0,
      cell: (account) => <RelativeTime value={account.last_login_at} fallback={t("accounts.table.never")} />,
    },
  ];
}

type Pending =
  | { kind: "invite"; recover: Account | null }
  | { kind: "role"; account: Account }
  | { kind: "disable"; account: Account }
  | { kind: "reset"; account: Account }
  | { kind: "remove"; account: Account }
  | { kind: "exception"; person: string }
  | null;

function RowActions({ t, account, self, onOpen }: { t: T; account: Account; self: boolean; onOpen: (pending: Pending) => void }) {
  const queryClient = useQueryClient();
  const status = accountStatus(account);
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: accountKeys.list });
  };
  const enable = useMutation({
    mutationFn: () => enableAccount(account.username),
    onSuccess: refresh,
    onError: (error) => {
      reportActionError(t("accounts.actions.enableFailed", { name: account.username }), error);
    },
  });
  const unlock = useMutation({
    mutationFn: () => unlockAccount(account.username),
    onSuccess: refresh,
    onError: (error) => {
      reportActionError(t("accounts.actions.unlockFailed", { name: account.username }), error);
    },
  });
  return (
    <Menu align="end" trigger={<IconButton label={t("accounts.table.actionsFor", { name: account.username })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
      {status === "locked" ? (
        <MenuItem icon={<LockOpen />} disabled={unlock.isPending} onClick={() => unlock.mutate()}>
          {t("accounts.actions.unlock")}
        </MenuItem>
      ) : null}
      <MenuItem icon={<UserCog />} disabled={self} onClick={() => onOpen({ kind: "role", account })}>
        {t("accounts.actions.changeRole")}
      </MenuItem>
      <MenuItem icon={<MailPlus />} onClick={() => onOpen({ kind: "invite", recover: account })}>
        {t("accounts.actions.newInvitation")}
      </MenuItem>
      {account.mfa_enabled ? (
        <MenuItem icon={<ShieldOff />} disabled={self} onClick={() => onOpen({ kind: "reset", account })}>
          {t("accounts.actions.resetFactor")}
        </MenuItem>
      ) : null}
      <MenuSeparator />
      {status === "disabled" ? (
        <MenuItem icon={<UserCheck />} disabled={enable.isPending} onClick={() => enable.mutate()}>
          {t("accounts.actions.enable")}
        </MenuItem>
      ) : (
        <MenuItem icon={<UserX />} disabled={self} onClick={() => onOpen({ kind: "disable", account })}>
          {t("accounts.actions.disable")}
        </MenuItem>
      )}
      <MenuItem icon={<ICONS.delete />} destructive disabled={self} onClick={() => onOpen({ kind: "remove", account })}>
        {t("accounts.actions.remove")}
      </MenuItem>
    </Menu>
  );
}

/** People holding roles that should not go together, and whether an exception covers them. */
function ConflictsNotice({ t, conflicts, canManage, onException }: { t: T; conflicts: readonly SeparationConflict[]; canManage: boolean; onException: (person: string) => void }) {
  const open = conflicts.filter((conflict) => !conflict.exception);
  if (open.length === 0) return null;
  const [first] = open;
  return (
    <Notice
      tone="warning"
      variant="banner"
      title={t("accounts.sod.conflictsTitle", { count: open.length })}
      {...(canManage && first !== undefined
        ? {
            action: (
              <Button size="sm" onClick={() => onException(first.person_ref)}>
                {t("accounts.sod.addException")}
              </Button>
            ),
          }
        : {})}
    >
      <ul className="flex flex-col gap-1">
        {open.map((conflict) => (
          <li key={conflict.person_ref}>
            {t.rich("accounts.sod.conflictLine", {
              person: <Mono key="person">{conflict.person_ref}</Mono>,
              accounts: conflict.accounts.map((entry) => `${entry["username"] ?? ""} (${roleLabel(t, entry["role"] ?? "")})`).join(", "),
            })}
          </li>
        ))}
      </ul>
    </Notice>
  );
}

function ExceptionsSection({ t, canManage, onAdd }: { t: T; canManage: boolean; onAdd: () => void }) {
  const queryClient = useQueryClient();
  const query = useQuery(exceptionsQuery());
  const [revoking, setRevoking] = useState<SodException | null>(null);
  const live = (query.data?.exceptions ?? []).filter((item) => item.revoked_at === null || item.revoked_at === undefined);
  const columns: Column<SodException>[] = [
    { id: "person", header: t("accounts.exceptions.person"), card: "title", cell: (item) => <Mono tone="default">{item.person_ref}</Mono> },
    { id: "reason", header: t("accounts.exceptions.reason"), cell: (item) => <span className="text-13 text-pretty">{item.reason}</span> },
    {
      id: "expires",
      header: t("accounts.exceptions.expires"),
      width: "w-36",
      cell: (item) =>
        item.in_force ? <RelativeTime value={item.expires_at} /> : <span className="text-13 text-fg-muted">{t("accounts.exceptions.expired")}</span>,
    },
  ];
  return (
    <Section
      title={t("accounts.exceptions.title")}
      description={t("accounts.exceptions.description")}
      {...(canManage
        ? {
            actions: (
              <Button size="sm" onClick={onAdd}>
                {t("accounts.exceptions.add")}
              </Button>
            ),
          }
        : {})}
    >
      {query.isError ? (
        <ErrorBlock compact error={query.error} title={t("accounts.exceptions.loadFailed")} onRetry={() => void query.refetch()} />
      ) : query.data !== undefined && live.length === 0 ? (
        <EmptyState variant="inline" title={t("accounts.exceptions.none")} />
      ) : (
        <DataTable
          caption={t("accounts.exceptions.caption")}
          columns={columns}
          rows={live}
          getRowId={(item) => String(item.id)}
          density="compact"
          mobile="cards"
          loading={query.isPending}
          skeletonRows={1}
          empty={<EmptyState variant="inline" title={t("accounts.exceptions.none")} />}
          {...(canManage
            ? {
                rowActions: (item: SodException) => (
                  <Button size="sm" variant="ghost" aria-label={t("accounts.exceptions.revokeLabel", { person: item.person_ref })} onClick={() => setRevoking(item)}>
                    <span className="max-sm:sr-only">{t("accounts.exceptions.revoke")}</span>
                  </Button>
                ),
              }
            : {})}
        />
      )}
      {revoking !== null ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(next) => {
            if (!next) setRevoking(null);
          }}
          title={t("accounts.exceptions.revokeTitle", { person: revoking.person_ref })}
          description={t("accounts.exceptions.revokeDescription")}
          actionLabel={t("accounts.exceptions.revokeAction")}
          onConfirm={async () => {
            await revokeException(revoking.id);
            void queryClient.invalidateQueries({ queryKey: accountKeys.exceptions });
            void queryClient.invalidateQueries({ queryKey: accountKeys.list });
          }}
        />
      ) : null}
    </Section>
  );
}

export interface AccountsSettingsProps {
  search: AccountsSearch;
  onSearchChange: (search: AccountsSearch, options?: { replace?: boolean }) => void;
}

/**
 * Settings > Accounts (the central's): who may sign in, with which role, and whether their
 * second factor is there. A security officer invites people, changes roles, disables, unlocks,
 * resets a lost factor and removes; an auditor reads. One account, one role; one person's
 * accounts may not hold roles that go against each other, save the documented exception.
 */
export function AccountsSettings({ search, onSearchChange }: AccountsSettingsProps) {
  const t = useT();
  useDocumentTitle(t("accounts.page.documentTitle"), 1);
  const queryClient = useQueryClient();
  const { data: session } = useQuery(sessionQuery());
  const query = useQuery(accountsQuery());
  const [pending, setPending] = useState<Pending>(null);
  const canManage = session?.permissions?.includes("accounts.manage") ?? false;
  const me = session?.account?.username ?? null;

  const all = useMemo(() => query.data?.accounts ?? [], [query.data]);
  const shown = useMemo(() => filterAccounts(all, search), [all, search]);
  const filtered = isFiltered(search);
  const empty = query.data !== undefined && all.length === 0;
  const columns = columnsFor(t);
  const set = (patch: { [K in keyof AccountsSearch]?: AccountsSearch[K] | undefined }, replace = false): void => {
    const next = { ...search, ...patch };
    onSearchChange(
      Object.fromEntries(Object.entries(next).filter(([, value]) => value !== undefined && value !== "")),
      { replace },
    );
  };
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: accountKeys.list });
  };

  const inviteButton = (variant: "primary" | "secondary") =>
    canManage ? (
      <Button variant={variant} icon={<UserPlus aria-hidden="true" />} onClick={() => setPending({ kind: "invite", recover: null })}>
        {t("accounts.page.invite")}
      </Button>
    ) : undefined;

  let content;
  if (query.isError && query.data === undefined) {
    content = <ErrorBlock error={query.error} title={t("accounts.page.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />;
  } else if (empty) {
    content = (
      <EmptyState
        variant="firstUse"
        icon={<Users />}
        title={t("accounts.page.emptyTitle")}
        description={t("accounts.page.emptyDescription")}
        action={
          session?.grant === "compat" ? (
            <Link to="/setup" search={{ next: "/settings/accounts" }} className={buttonClassName("secondary", "md")}>
              {t("accounts.page.createFirst")}
            </Link>
          ) : (
            inviteButton("secondary")
          )
        }
        command="noust user create NAME --role admin"
        level={3}
      />
    );
  } else {
    content = (
      <div className="flex min-w-0 flex-col gap-4">
        <FilterBar
          label={t("accounts.page.filterLabel")}
          search={{
            value: search.q ?? "",
            onChange: (value) => set({ q: value }, true),
            label: t("accounts.page.searchLabel"),
            placeholder: t("accounts.page.searchPlaceholder"),
          }}
          filters={
            <>
              <Select
                aria-label={t("accounts.page.roleFilter")}
                value={search.role ?? ALL}
                onValueChange={(value) => set({ role: ROLES.find((role) => role === value) })}
                options={[{ value: ALL, label: t("accounts.page.everyRole") }, ...ROLES.map((role) => ({ value: role, label: roleLabel(t, role) }))]}
                className="min-w-36"
              />
              <Select
                aria-label={t("accounts.page.stateFilter")}
                value={search.state ?? ALL}
                onValueChange={(value) => set({ state: STATE_FILTERS.find((state) => state === value) })}
                options={[
                  { value: ALL, label: t("accounts.page.everyState") },
                  { value: "active", label: t("accounts.state.active") },
                  { value: "locked", label: t("accounts.state.locked") },
                  { value: "disabled", label: t("accounts.state.disabled") },
                  { value: "invited", label: t("accounts.state.invited") },
                ]}
                className="min-w-36"
              />
            </>
          }
          {...(filtered
            ? {
                actions: (
                  <Button variant="ghost" onClick={() => onSearchChange({})}>
                    {t("accounts.page.clearFilters")}
                  </Button>
                ),
              }
            : {})}
          {...(query.data !== undefined
            ? {
                count: filtered
                  ? t("accounts.page.countFiltered", { shown: shown.length, total: all.length })
                  : t("accounts.page.count", { count: all.length }),
              }
            : {})}
        />
        <DataTable
          caption={t("accounts.page.caption")}
          columns={columns}
          rows={shown}
          getRowId={(account) => account.username}
          mobile="cards"
          loading={query.isPending}
          skeletonRows={3}
          {...(canManage ? { rowActions: (account: Account) => <RowActions t={t} account={account} self={account.username === me} onOpen={setPending} /> } : {})}
          empty={
            <EmptyState
              variant="inline"
              title={t("accounts.page.noMatch")}
              action={
                <Button size="sm" variant="ghost" onClick={() => onSearchChange({})}>
                  {t("accounts.page.clearFilters")}
                </Button>
              }
            />
          }
        />
      </div>
    );
  }

  return (
    <Sections>
      {query.data !== undefined ? (
        <ConflictsNotice t={t} conflicts={query.data.conflicts} canManage={canManage} onException={(person) => setPending({ kind: "exception", person })} />
      ) : null}
      <Section title={t("accounts.page.title")} description={t("accounts.page.description")} {...(empty ? {} : { actions: inviteButton("primary") })}>
        {content}
      </Section>
      {!empty ? <ExceptionsSection t={t} canManage={canManage} onAdd={() => setPending({ kind: "exception", person: "" })} /> : null}
      {!empty ? <AccessReviewSection canManage={canManage} /> : null}
      <CommandHint label={t("accounts.page.fromTerminal")} command="noust user list" />

      {pending?.kind === "invite" ? <InviteDialog open accounts={all} recover={pending.recover} onClose={() => setPending(null)} /> : null}
      {pending?.kind === "role" ? <RoleDialog account={pending.account} accounts={all} onClose={() => setPending(null)} /> : null}
      {pending?.kind === "disable" ? <DisableDialog account={pending.account} onClose={() => setPending(null)} /> : null}
      {pending?.kind === "exception" ? <ExceptionDialog person={pending.person} onClose={() => setPending(null)} /> : null}
      {pending?.kind === "reset" ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(next) => {
            if (!next) setPending(null);
          }}
          title={t("accounts.reset.title", { name: pending.account.username })}
          description={t("accounts.reset.description")}
          actionLabel={t("accounts.reset.action")}
          onConfirm={async () => {
            await resetMfa(pending.account.username);
            refresh();
            toast.success(t("accounts.reset.doneToast", { name: pending.account.username }));
          }}
        />
      ) : null}
      {pending?.kind === "remove" ? (
        <ConfirmDialog
          friction="type"
          confirmText={pending.account.username}
          destructive
          open
          onOpenChange={(next) => {
            if (!next) setPending(null);
          }}
          title={t("accounts.remove.title", { name: pending.account.username })}
          description={t("accounts.remove.description")}
          actionLabel={t("accounts.remove.action")}
          onConfirm={async () => {
            await removeAccount(pending.account.username);
            refresh();
          }}
        />
      ) : null}
    </Sections>
  );
}
