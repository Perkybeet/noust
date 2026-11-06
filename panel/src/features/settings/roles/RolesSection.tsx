import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Eye, Pencil, RotateCcw } from "lucide-react";
import { useState } from "react";

import { sessionQuery } from "../../../api/queries/auth";
import { deleteRole, resetRole, rolesChanged, rolesQuery } from "../../../api/queries/roles";
import type { Role } from "../../../api/queries/roles";
import { QueryState } from "../../../components/page/QueryState";
import { GuardedButton } from "../../../components/page/permissions";
import { RowMenu } from "../../../components/page/RowMenu";
import { Badge } from "../../../components/ui/Badge";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { HelpPopover } from "../../../components/ui/HelpPopover";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { SettingsPrimaryAction } from "../SettingsShell";
import { DuplicateRoleDialog } from "./DuplicateRoleDialog";
import { RoleEditor } from "./RoleEditor";
import { kindOf, roleDescription, roleLabel } from "./roleModel";

type Open = { kind: "edit"; role: Role | null } | { kind: "duplicate"; role: Role } | { kind: "reset"; role: Role } | { kind: "delete"; role: Role } | null;

function columnsFor(t: T): Column<Role>[] {
  return [
    {
      id: "role",
      header: t("roles.column.role"),
      card: "title",
      cell: (role) => (
        <span className="flex min-w-0 flex-col items-start gap-0.5">
          <span className="text-13 font-medium text-fg">{roleLabel(t, role)}</span>
          <span className="max-w-measure text-12 text-pretty text-fg-muted">{roleDescription(t, role)}</span>
        </span>
      ),
    },
    {
      id: "kind",
      header: t("roles.column.kind"),
      card: "status",
      width: "w-56",
      cell: (role) => {
        const kind = kindOf(role);
        return (
          <span className="flex flex-wrap items-center gap-1.5">
            <Badge>{t(`roles.kind.${kind}`)}</Badge>
            <HelpPopover label={t(`roles.kind.${kind}`)}>
              <p className="max-w-measure text-13 text-pretty">{t(`roles.kindHelp.${kind}`)}</p>
            </HelpPopover>
            {(role.offers ?? []).length > 0 ? <StatusPill state="warning" size="sm" appearance="inline" label={t("roles.offers.badge", { count: (role.offers ?? []).length })} /> : null}
            {role.joins_duties ? (
              <HelpPopover label={t("roles.joins.label")}>
                <p className="max-w-measure text-13 text-pretty">{t("roles.joins.help")}</p>
                <p className="mt-2 text-13">
                  <span className="font-medium">{t("roles.joins.governing")}</span> <Mono>{(role.governing ?? []).join(", ")}</Mono>
                </p>
                <p className="mt-1 text-13">
                  <span className="font-medium">{t("roles.joins.running")}</span> <Mono>{(role.running ?? []).join(", ")}</Mono>
                </p>
              </HelpPopover>
            ) : null}
          </span>
        );
      },
    },
    { id: "people", header: t("roles.column.people"), width: "w-28", hideBelow: "sm", cell: (role) => <span className="text-13 text-fg-muted">{t("roles.people", { count: role.holder_count })}</span> },
    {
      id: "permissions",
      header: t("roles.column.permissions"),
      width: "w-32",
      hideBelow: "md",
      cell: (role) => <span className="text-13 text-fg-muted">{t("roles.permissionCount", { count: role.permissions.filter((name) => name !== "self").length })}</span>,
    },
  ];
}

/**
 * The roles, one list (item 96): the five Noust ships and the owner first, then yours. Every one but the owner
 * is edited, duplicated and, if it was edited or shipped, put back as it comes; one of your own is deleted
 * while nobody has it. The server decides each of those (`can_edit`, `can_reset`, `can_delete`) and says why
 * when it refuses; the page offers what the server admits.
 */
export function RolesSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const { data: session } = useQuery(sessionQuery());
  const canManage = session?.permissions?.includes("accounts.manage") ?? false;
  const query = useQuery(rolesQuery());
  const [open, setOpen] = useState<Open>(null);
  const columns = columnsFor(t);
  const refresh = (): void => {
    rolesChanged(queryClient);
  };

  return (
    <>
      <SettingsPrimaryAction>
        <GuardedButton permission="accounts.manage" variant="primary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setOpen({ kind: "edit", role: null })}>
          {t("roles.newRole")}
        </GuardedButton>
      </SettingsPrimaryAction>
      <QueryState
        query={query}
        label={t("roles.loading")}
        skeleton={<DataTable caption={t("roles.tableCaption")} columns={columns} rows={[]} getRowId={(role) => role.name} loading skeletonRows={6} embedded />}
      >
        {(data) => (
          <>
            <DataTable
              caption={t("roles.tableCaption")}
              columns={columns}
              rows={data.roles}
              embedded
              getRowId={(role) => role.name}
              onRowActivate={(role) => setOpen({ kind: "edit", role })}
              rowActions={(role) => (
                <RowMenu name={roleLabel(t, role)}>
                  <MenuItem icon={role.can_edit && canManage ? <Pencil /> : <Eye />} onClick={() => setOpen({ kind: "edit", role })}>
                    {role.can_edit && canManage ? t("common.words.edit") : t("roles.actions.view")}
                  </MenuItem>
                  {canManage && role.can_duplicate ? (
                    <MenuItem icon={<Copy />} onClick={() => setOpen({ kind: "duplicate", role })}>
                      {t("roles.actions.duplicate")}
                    </MenuItem>
                  ) : null}
                  {canManage && role.can_reset ? (
                    <MenuItem icon={<RotateCcw />} onClick={() => setOpen({ kind: "reset", role })}>
                      {t("roles.actions.reset")}
                    </MenuItem>
                  ) : null}
                  {canManage && role.can_delete ? (
                    <>
                      <MenuSeparator />
                      <MenuItem icon={<ICONS.delete />} destructive onClick={() => setOpen({ kind: "delete", role })}>
                        {t("common.words.delete")}
                      </MenuItem>
                    </>
                  ) : null}
                </RowMenu>
              )}
            />
            {open?.kind === "edit" ? <RoleEditor role={open.role === null ? null : canManage ? open.role : { ...open.role, can_edit: false }} catalog={data} onClose={() => setOpen(null)} /> : null}
          </>
        )}
      </QueryState>
      {open?.kind === "duplicate" ? <DuplicateRoleDialog role={open.role} onClose={() => setOpen(null)} /> : null}
      {open?.kind === "reset" ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(next) => {
            if (!next) setOpen(null);
          }}
          title={t("roles.reset.title", { role: roleLabel(t, open.role) })}
          description={t("roles.reset.description")}
          actionLabel={t("roles.reset.action")}
          onConfirm={async () => {
            await resetRole(open.role.name);
            refresh();
          }}
        />
      ) : null}
      {open?.kind === "delete" ? (
        <ConfirmDialog
          friction="simple"
          destructive
          open
          onOpenChange={(next) => {
            if (!next) setOpen(null);
          }}
          title={t("roles.delete.title", { role: roleLabel(t, open.role) })}
          description={t("roles.delete.description")}
          actionLabel={t("roles.delete.action")}
          onConfirm={async () => {
            await deleteRole(open.role.name);
            refresh();
          }}
        />
      ) : null}
    </>
  );
}
