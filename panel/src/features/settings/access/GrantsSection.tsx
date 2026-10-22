import { useQuery, useQueryClient } from "@tanstack/react-query";
import { UsersRound } from "lucide-react";
import { useState } from "react";

import { accessBindingsQuery, accessKeys, accessRolesQuery, isScopeKind, unbindRole } from "../../../api/queries/access";
import type { AccessBinding } from "../../../api/queries/access";
import { QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { TextLink } from "../../../components/ui/TextLink";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { RowAction } from "../RowAction";
import { SettingsPrimaryAction } from "../SettingsShell";
import { GrantDialog } from "./GrantDialog";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

function columnsFor(t: T, roleLabel: (name: string) => string, isCustom: (name: string) => boolean): Column<AccessBinding>[] {
  return [
    {
      id: "account",
      header: t("access.grants.column.account"),
      card: "title",
      cell: (binding) => <Mono tone="default">{binding.username}</Mono>,
    },
    {
      id: "role",
      header: t("access.grants.column.role"),
      card: "status",
      cell: (binding) => (
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-13 text-fg">{roleLabel(binding.role)}</span>
          {isCustom(binding.role) ? <Badge>{t("access.grants.custom")}</Badge> : null}
        </span>
      ),
    },
    {
      id: "where",
      header: t("access.grants.column.where"),
      cell: (binding) => (
        <span className="flex flex-wrap items-center gap-2">
          {isScopeKind(binding.scope_kind) ? <Badge>{t(`access.scopeShort.${binding.scope_kind}`)}</Badge> : <Badge>{binding.scope_kind}</Badge>}
          <Mono tone="default">{binding.scope}</Mono>
        </span>
      ),
    },
    {
      id: "granted",
      header: t("access.grants.column.granted"),
      hideBelow: "md",
      cell: (binding) => <RelativeTime value={binding.created_at} />,
    },
  ];
}

/**
 * Who holds which role where. A grant on a project or an environment is what lets the same person
 * administer one and only read another; a team membership appears here too but is managed in the
 * team, as it always was. Revoking takes the role from that place only.
 */
export function GrantsSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery(accessBindingsQuery());
  const roles = useQuery(accessRolesQuery());
  const [granting, setGranting] = useState(false);
  const [revoking, setRevoking] = useState<AccessBinding | null>(null);

  const byName = new Map((roles.data?.roles ?? []).map((role) => [role.name, role]));
  const roleLabel = (name: string): string => byName.get(name)?.label ?? name;
  const isCustom = (name: string): boolean => byName.get(name)?.built_in === false;
  const columns = columnsFor(t, roleLabel, isCustom);

  const grant = (
    <Button
      variant="primary"
      icon={<ICONS.add aria-hidden="true" />}
      onClick={() => {
        setGranting(true);
      }}
    >
      {t("access.grants.grantRole")}
    </Button>
  );

  return (
    <Section title={t("access.grants.title")} icon={OBJECT_ICONS.role} description={t("access.grants.description")}>
      {query.data !== undefined && query.data.bindings.length > 0 ? <SettingsPrimaryAction>{grant}</SettingsPrimaryAction> : null}
      <QueryState
        query={query}
        label={t("access.loadingLabel")}
        skeleton={<DataTable caption={t("access.grants.tableCaption")} columns={columns} rows={[]} getRowId={(binding) => String(binding.id)} loading skeletonRows={3} />}
        isEmpty={(data) => data.bindings.length === 0}
        empty={<EmptyState variant="firstUse" level={3} icon={<UsersRound />} title={t("access.grants.emptyTitle")} description={t("access.grants.emptyDescription")} action={grant} command="noust access grant maria --role admin --team acme --project web" />}
      >
        {(data) => (
          <DataTable
            caption={t("access.grants.tableCaption")}
            columns={columns}
            rows={data.bindings}
            getRowId={(binding) => `${String(binding.id)}-${binding.username}-${binding.scope}`}
            pagination="access-grants"
            rowActions={(binding) =>
              binding.id === 0 ? (
                binding.team !== null && binding.team !== undefined ? (
                  <TextLink to="/teams/$team/members" params={{ team: binding.team }} size="ui">
                    {t("access.grants.manageInTeam")}
                  </TextLink>
                ) : null
              ) : (
                <RowAction
                  label={t("access.grants.revokeLabel", { role: roleLabel(binding.role), account: binding.username })}
                  text={t("common.words.revoke")}
                  icon={<ICONS.delete />}
                  onClick={() => {
                    setRevoking(binding);
                  }}
                />
              )
            }
          />
        )}
      </QueryState>
      {granting ? (
        <GrantDialog
          onClose={() => {
            setGranting(false);
          }}
        />
      ) : null}
      {revoking !== null ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(next) => {
            if (!next) setRevoking(null);
          }}
          title={t("access.grants.revokeDialog.title", { role: roleLabel(revoking.role), account: revoking.username })}
          description={t("access.grants.revokeDialog.description", { role: roleLabel(revoking.role), account: revoking.username, where: revoking.scope })}
          actionLabel={t("access.grants.revokeDialog.action")}
          destructive
          onConfirm={async () => {
            await unbindRole(revoking.id);
            toast.success(t("access.grants.revokedToast", { role: roleLabel(revoking.role), account: revoking.username }));
            void queryClient.invalidateQueries({ queryKey: accessKeys.all });
          }}
        />
      ) : null}
    </Section>
  );
}
