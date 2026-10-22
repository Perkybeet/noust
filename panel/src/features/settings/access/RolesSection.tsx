import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Pencil } from "lucide-react";
import { useState } from "react";

import { accessKeys, accessRolesQuery, deleteRole, isScopeKind } from "../../../api/queries/access";
import type { AccessRole } from "../../../api/queries/access";
import { QueryState } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { RowAction } from "../RowAction";
import { RoleDrawer } from "./RoleDrawer";

function placesOf(t: T, role: AccessRole): string {
  return (role.scopes ?? [])
    .filter(isScopeKind)
    .map((kind) => t(`access.scopeShort.${kind}`))
    .join(", ");
}

function columnsFor(t: T): Column<AccessRole>[] {
  return [
    {
      id: "role",
      header: t("access.roles.column.role"),
      card: "title",
      cell: (role) => (
        <span className="flex min-w-0 flex-col items-start gap-0.5">
          <span className="text-13 font-medium text-fg">{role.label}</span>
          <Mono tone="faint">{role.name}</Mono>
        </span>
      ),
    },
    {
      id: "kind",
      header: t("access.roles.column.kind"),
      card: "status",
      width: "w-28",
      cell: (role) => <Badge>{role.built_in ? t("access.roles.builtIn") : t("access.roles.custom")}</Badge>,
    },
    {
      id: "where",
      header: t("access.roles.column.where"),
      hideBelow: "md",
      cell: (role) => <span className="text-13 text-fg-muted">{placesOf(t, role)}</span>,
    },
    {
      id: "permissions",
      header: t("access.roles.column.permissions"),
      hideBelow: "sm",
      cell: (role) => <span className="text-13 text-fg-muted">{t("access.roles.permissionCount", { count: role.permissions.filter((name) => name !== "self").length })}</span>,
    },
  ];
}

/**
 * The roles: the built-in team roles, which only show what they hold, and the roles the operator
 * defines, which can be changed and deleted. A role nobody holds can be deleted; the server
 * refuses one somebody holds and says who.
 */
export function RolesSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery(accessRolesQuery());
  const [open, setOpen] = useState<AccessRole | "new" | null>(null);
  const [deleting, setDeleting] = useState<AccessRole | null>(null);
  const columns = columnsFor(t);

  return (
    <Section
      title={t("access.roles.title")}
      description={t("access.roles.description")}
      actions={
        <Button
          variant="secondary"
          icon={<ICONS.add aria-hidden="true" />}
          onClick={() => {
            setOpen("new");
          }}
        >
          {t("access.roles.defineRole")}
        </Button>
      }
    >
      <QueryState
        query={query}
        label={t("access.loadingLabel")}
        skeleton={<DataTable caption={t("access.roles.tableCaption")} columns={columns} rows={[]} getRowId={(role) => role.name} loading mobile="cards" skeletonRows={4} />}
      >
        {(data) => (
          <>
            <DataTable
              caption={t("access.roles.tableCaption")}
              columns={columns}
              rows={data.roles}
              getRowId={(role) => role.name}
              mobile="cards"
              rowActions={(role) =>
                role.built_in ? (
                  <RowAction
                    label={t("access.roles.viewLabel", { role: role.label })}
                    text={t("access.roles.view")}
                    icon={<Eye />}
                    onClick={() => {
                      setOpen(role);
                    }}
                  />
                ) : (
                  <span className="flex items-center gap-1">
                    <RowAction
                      label={t("access.roles.editLabel", { role: role.label })}
                      text={t("common.words.edit")}
                      icon={<Pencil />}
                      onClick={() => {
                        setOpen(role);
                      }}
                    />
                    <RowAction
                      label={t("access.roles.deleteLabel", { role: role.label })}
                      text={t("common.words.delete")}
                      icon={<ICONS.delete />}
                      onClick={() => {
                        setDeleting(role);
                      }}
                    />
                  </span>
                )
              }
            />
            {open !== null ? (
              <RoleDrawer
                role={open === "new" ? null : open}
                catalog={data}
                onClose={() => {
                  setOpen(null);
                }}
              />
            ) : null}
          </>
        )}
      </QueryState>
      {deleting !== null ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(next) => {
            if (!next) setDeleting(null);
          }}
          title={t("access.roles.deleteDialog.title", { role: deleting.label })}
          description={t("access.roles.deleteDialog.description")}
          actionLabel={t("access.roles.deleteDialog.action")}
          destructive
          onConfirm={async () => {
            await deleteRole(deleting.name);
            toast.success(t("access.roles.deletedToast", { role: deleting.label }));
            void queryClient.invalidateQueries({ queryKey: accessKeys.all });
          }}
        />
      ) : null}
    </Section>
  );
}
