import { useQuery } from "@tanstack/react-query";

import { rolesQuery } from "../../../api/queries/roles";
import { QueryState } from "../../../components/page/QueryState";
import { Subsection } from "../../../components/page/Subsection";
import { Badge } from "../../../components/ui/Badge";
import { Drawer } from "../../../components/ui/Drawer";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { Account } from "../accounts/api";
import { rolesOf } from "./AccountRolesDialog";
import { effectivePermissions, groupPermissions, roleLabel } from "./roleModel";

/**
 * What a person can do, from all their roles together, and which role gives each permission: the answer to
 * "why can she do this?" that a list of roles does not give (item 96). The account's `permissions` are what the
 * server computed; the roles say where each comes from. The owner holds everything and is told as one line.
 */
export function PermissionsDrawer({ account, onClose }: { account: Account; onClose: () => void }) {
  const t = useT();
  const roles = useQuery(rolesQuery());
  const held = rolesOf(account);
  return (
    <Drawer
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      size="md"
      title={t("roles.account.effective", { name: account.display_name !== "" ? account.display_name : account.username })}
      description={t("roles.account.effectiveDescription")}
    >
      <QueryState query={roles} label={t("roles.loading")} skeleton={<Skeleton className="h-48 w-full rounded-card" />}>
        {(data) => {
          const names = new Map(data.roles.map((role) => [role.name, role]));
          const labelOf = (name: string): string => {
            const role = names.get(name);
            return role === undefined ? name : roleLabel(t, role);
          };
          const granted = effectivePermissions(held, data.roles);
          const groups = groupPermissions(granted.map((item) => item.permission));
          const from = new Map(granted.map((item) => [item.permission, item.from]));
          return (
            <div className="flex flex-col gap-5">
              <ul aria-label={t("roles.column.role")} className="flex flex-wrap gap-1.5">
                {held.map((name) => (
                  <li key={name}>
                    <Badge>{labelOf(name)}</Badge>
                  </li>
                ))}
              </ul>
              <p className="text-13 text-fg-muted">{account.owner ? t("roles.account.owner") : t("roles.account.permissionTotal", { count: granted.length })}</p>
              {granted.length === 0 ? <p className="text-13 text-fg-muted">{t("roles.account.noPermissions")}</p> : null}
              {groups.map((group) => (
                <Subsection key={group.area} title={group.area}>
                  <ul className="flex flex-col gap-2">
                    {group.permissions.map((permission) => {
                      const givers = (from.get(permission) ?? []).map(labelOf);
                      return (
                        <li key={permission} className="flex min-w-0 flex-col gap-0.5">
                          <Mono tone="default">{permission}</Mono>
                          <span className="text-12 text-fg-muted" translate="no">
                            {data.permissions[permission] ?? ""}
                          </span>
                          {held.length > 1 ? <span className="text-12 text-fg-faint">{t("roles.account.givenBy", { roles: givers.join(", ") })}</span> : null}
                        </li>
                      );
                    })}
                  </ul>
                </Subsection>
              ))}
            </div>
          );
        }}
      </QueryState>
    </Drawer>
  );
}
