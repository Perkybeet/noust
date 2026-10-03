import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useMemo, useState } from "react";

import { apiTokensQuery, authKeys, revokeApiToken, sessionQuery } from "../../api/queries/auth";
import type { ApiToken } from "../../api/queries/auth";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section, Sections } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { accountsQuery } from "./accounts/api";
import { CreateTokenDialog } from "./CreateTokenDialog";
import { RowAction } from "./RowAction";
import { sortTokens, tokenState } from "./tokens";
import type { TokenState } from "./tokens";
import { SettingsPrimaryAction } from "./SettingsShell";

/**
 * A token's state, an on/off at a glance (item 56): an active token works, in the running
 * green; an expired or revoked one does not, in the stopped grey.
 */
function TokenStateLabel({ t, state }: { t: T; state: TokenState }) {
  const label =
    state === "active" ? t("settings.tokens.state.active") : state === "expired" ? t("settings.tokens.state.expired") : t("settings.tokens.state.revoked");
  return <StatusPill state={state === "active" ? "running" : "stopped"} label={label} appearance="inline" size="sm" />;
}

function columnsFor(t: T, ownerName: (token: ApiToken) => string | null): Column<ApiToken>[] {
  return [
    {
      id: "name",
      header: t("settings.tokens.columnName"),
      card: "title",
      cell: (token) => (
        <span className="flex min-w-0 flex-col items-start gap-1">
          <Mono tone="default" truncate>
            {token.name}
          </Mono>
          <Badge mono>{token.scope}</Badge>
        </span>
      ),
    },
    { id: "state", header: t("settings.tokens.columnState"), card: "status", width: "w-32", cell: (token) => <TokenStateLabel t={t} state={tokenState(token)} /> },
    {
      id: "owner",
      header: t("accounts.tokens.columnOwner"),
      hideBelow: "sm",
      cell: (token) => {
        const owner = ownerName(token);
        return owner === null ? <EmptyCell reason={t("accounts.tokens.noOwner")} /> : <Mono tone="default">{owner}</Mono>;
      },
    },
    {
      id: "addresses",
      header: t("accounts.tokens.columnAddresses"),
      hideBelow: "lg",
      cell: (token) =>
        token.allowed_cidrs && token.allowed_cidrs.length > 0 ? (
          <Mono tone="default">{token.allowed_cidrs.join(", ")}</Mono>
        ) : (
          <span className="text-13 text-fg-muted">{t("accounts.tokens.anyAddress")}</span>
        ),
    },
    {
      id: "expires",
      header: t("settings.tokens.columnExpires"),
      hideBelow: "sm",
      cell: (token) =>
        token.revoked_at !== null && token.revoked_at !== undefined ? (
          <EmptyCell reason={t("settings.tokens.revokedNotApplicable")} />
        ) : (
          <RelativeTime value={token.expires_at} fallback={t("settings.tokens.never")} />
        ),
    },
    {
      id: "last-used",
      header: t("settings.tokens.columnLastUsed"),
      hideBelow: "md",
      cell: (token) => (
        <span className="flex flex-col">
          <RelativeTime value={token.last_used_at} fallback={t("settings.tokens.never")} />
          {token.last_used_ip ? <Mono tone="faint">{token.last_used_ip}</Mono> : null}
        </span>
      ),
    },
  ];
}

/**
 * Settings > API tokens (the central's): named credentials for CI and scripts. Each belongs to
 * a person and never does more than they may, may be limited to some addresses, and expires.
 */
export function TokensSettings() {
  const t = useT();
  useDocumentTitle(t("settings.tokens.documentTitle"), 1);
  const queryClient = useQueryClient();
  const { data: session } = useQuery(sessionQuery());
  const query = useQuery(apiTokensQuery());
  // Owners by name, for whoever may read the accounts (a security officer, an auditor).
  const accounts = useQuery({ ...accountsQuery(), enabled: session?.permissions?.includes("accounts.read") ?? false });
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);
  const names = useMemo(() => new Map((accounts.data?.accounts ?? []).map((account) => [account.id, account.username])), [accounts.data]);
  const ownerName = (token: ApiToken): string | null => {
    const id = token.owner_account_id;
    if (id === null || id === undefined) return null;
    if (session?.account?.id === id) return session.account.username;
    return names.get(id) ?? token.created_by ?? `#${String(id)}`;
  };
  const columns = columnsFor(t, ownerName);

  const create = (
    <Button variant="primary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setCreating(true)}>
      {t("settings.tokens.createToken")}
    </Button>
  );

  return (
    <Sections>
      {query.data !== undefined && query.data.tokens.length > 0 ? <SettingsPrimaryAction>{create}</SettingsPrimaryAction> : null}
      <Section
        title={t("accounts.tokens.title")}
        description={t("accounts.tokens.description")}
      >
        <QueryState
          query={query}
          label={t("settings.tokens.loadingLabel")}
          skeleton={<DataTable caption={t("settings.tokens.tableCaption")} columns={columns} rows={[]} getRowId={(token) => String(token.id)} loading mobile="cards" skeletonRows={2} />}
          isEmpty={(data) => data.tokens.length === 0}
          empty={
            <EmptyState
              variant="firstUse"
              level={3}
              icon={<KeyRound />}
              title={t("settings.tokens.emptyTitle")}
              description={t("accounts.tokens.emptyDescription")}
              action={create}
              command="noust token create ci-deploy --scope deploy --expires-hours 2160 --owner NAME"
            />
          }
        >
          {(data) => (
            <DataTable
              caption={t("settings.tokens.tableCaption")}
              columns={columns}
              rows={sortTokens(data.tokens)}
              getRowId={(token) => String(token.id)}
              mobile="cards"
              rowActions={(token) =>
                tokenState(token) === "active" ? (
                  <RowAction
                    label={t("settings.tokens.revokeLabel", { name: token.name })}
                    text={t("settings.tokens.revokeText")}
                    icon={<ICONS.delete />}
                    onClick={() => {
                      setRevoking(token);
                    }}
                  />
                ) : null
              }
            />
          )}
        </QueryState>
      </Section>
      {/* Under a list of unknown length: drawn with it, never pushed down the page by it. */}
      {query.data !== undefined || query.isError ? (
        <Section title={t("settings.tokens.usingTitle")} description={t("settings.tokens.usingDescription")}>
          <CommandHint command={`curl -H "Authorization: Bearer noust_tok_..." ${window.location.origin}/api/apps`} />
        </Section>
      ) : null}

      <CreateTokenDialog
        open={creating}
        onClose={() => {
          setCreating(false);
        }}
      />
      {revoking !== null ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(next) => {
            if (!next) setRevoking(null);
          }}
          title={t("settings.tokens.revokeDialogTitle", { name: revoking.name })}
          description={t("settings.tokens.revokeDialogDescription")}
          actionLabel={t("settings.tokens.revokeAction")}
          onConfirm={async () => {
            const result = await revokeApiToken(revoking.id);
            toast.success(t("settings.tokens.revokedToast", { name: result.revoked }));
            void queryClient.invalidateQueries({ queryKey: authKeys.tokens });
          }}
        />
      ) : null}
    </Sections>
  );
}
