import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, KeyRound, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { apiTokensQuery, authKeys, revokeApiToken } from "../../api/queries/auth";
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
import { EmptyState } from "../../components/ui/EmptyState";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { CreateTokenDialog } from "./CreateTokenDialog";
import { RowAction } from "./RowAction";
import { sortTokens, tokenState } from "./tokens";
import type { TokenState } from "./tokens";

/** A token's state told three ways: colour, shape and word. */
function TokenStateLabel({ t, state }: { t: T; state: TokenState }) {
  const label =
    state === "active"
      ? t("settings.tokens.state.active")
      : state === "expired"
        ? t("settings.tokens.state.expired")
        : t("settings.tokens.state.revoked");
  return (
    <span className="flex items-center gap-1.5">
      {state === "active" ? (
        <StatusGlyph state="running" className="text-ok" />
      ) : state === "expired" ? (
        <StatusGlyph state="stopped" className="text-idle" />
      ) : (
        <Ban aria-hidden="true" className="size-3 text-idle" />
      )}
      <span className={state === "active" ? "text-fg" : "text-fg-muted"}>{label}</span>
    </span>
  );
}

function columnsFor(t: T): readonly Column<ApiToken>[] {
  return [
    {
      id: "name",
      header: t("settings.tokens.columnName"),
      cell: (token) => (
        <span className="flex flex-col items-start gap-1">
          <span translate="no" className="mono text-12">
            {token.name}
          </span>
          {/* On a phone the scope column is hidden; the scope rides under the name. */}
          <Badge mono className="sm:hidden">
            {token.scope}
          </Badge>
        </span>
      ),
    },
    {
      id: "scope",
      header: t("settings.tokens.columnScope"),
      hideBelow: "sm",
      cell: (token) => <Badge mono>{token.scope}</Badge>,
    },
    { id: "state", header: t("settings.tokens.columnState"), cell: (token) => <TokenStateLabel t={t} state={tokenState(token)} /> },
    {
      id: "created",
      header: t("settings.tokens.columnCreated"),
      hideBelow: "md",
      cell: (token) => <RelativeTime value={token.created_at} />,
    },
    {
      id: "expires",
      header: t("settings.tokens.columnExpires"),
      hideBelow: "sm",
      cell: (token) =>
        token.revoked_at !== null && token.revoked_at !== undefined ? (
          <span className="sr-only">{t("settings.tokens.revokedNotApplicable")}</span>
        ) : (
          <RelativeTime value={token.expires_at} fallback={t("settings.tokens.never")} />
        ),
    },
    {
      id: "last-used",
      header: t("settings.tokens.columnLastUsed"),
      hideBelow: "lg",
      cell: (token) => <RelativeTime value={token.last_used_at} fallback={t("settings.tokens.never")} />,
    },
  ];
}

/** Settings > API tokens: named, scoped credentials for CI and scripts. */
export function TokensSettings() {
  const t = useT();
  useDocumentTitle(t("settings.tokens.documentTitle"), 1);
  const queryClient = useQueryClient();
  const query = useQuery(apiTokensQuery());
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);
  const [confirming, setConfirming] = useState(false);
  const columns = columnsFor(t);

  const create = (
    <Button
      variant="primary"
      icon={<Plus aria-hidden="true" />}
      onClick={() => {
        setCreating(true);
      }}
    >
      {t("settings.tokens.createToken")}
    </Button>
  );

  return (
    <Sections>
      <Section
        title={t("settings.tokens.sectionTitle")}
        description={t("settings.tokens.sectionDescription")}
        actions={query.data !== undefined && query.data.tokens.length > 0 ? create : undefined}
      >
        <QueryState
          query={query}
          label={t("settings.tokens.loadingLabel")}
          skeleton={<DataTable caption={t("settings.tokens.tableCaption")} columns={columns} rows={[]} getRowId={(token) => String(token.id)} loading />}
          isEmpty={(data) => data.tokens.length === 0}
          empty={
            <EmptyState
              icon={<KeyRound />}
              title={t("settings.tokens.emptyTitle")}
              description={t("settings.tokens.emptyDescription")}
              action={create}
            />
          }
        >
          {(data) => (
            <DataTable
              caption={t("settings.tokens.tableCaption")}
              columns={columns}
              rows={sortTokens(data.tokens)}
              getRowId={(token) => String(token.id)}
              rowActions={(token) =>
                tokenState(token) === "active" ? (
                  <RowAction
                    label={t("settings.tokens.revokeLabel", { name: token.name })}
                    text={t("settings.tokens.revokeText")}
                    icon={<Trash2 />}
                    onClick={() => {
                      setRevoking(token);
                      setConfirming(true);
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
        <Section
          title={t("settings.tokens.usingTitle")}
          description={t("settings.tokens.usingDescription")}
        >
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
          open={confirming}
          onOpenChange={setConfirming}
          title={t("settings.tokens.revokeDialogTitle", { name: revoking.name })}
          description={t("settings.tokens.revokeDialogDescription")}
          confirmText={revoking.name}
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
