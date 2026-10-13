import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { accessKeys, createRole, updateRole } from "../../../api/queries/access";
import type { AccessRole, AccessRoles } from "../../../api/queries/access";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Textarea } from "../../../components/ui/Textarea";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { splitErrors } from "../formErrors";
import { groupPermissions, roleScopes } from "./accessModel";

const FIELDS = ["name", "label", "description", "permissions"] as const;

export interface RoleDrawerProps {
  /** The role being looked at or changed, or null to define one. */
  role: AccessRole | null;
  catalog: AccessRoles;
  onClose: () => void;
}

/**
 * Defining a role, changing one of your own, or looking at what a built-in one holds. The
 * permissions are chosen from the ones that exist, each with the server's own sentence on what it
 * means; where the role can be held is said as the choice is made, from the ceilings the server
 * publishes, and the server has the last word.
 */
export function RoleDrawer({ role, catalog, onClose }: RoleDrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const creating = role === null;
  const readOnly = role?.built_in === true;
  const [name, setName] = useState(role?.name ?? "");
  const [label, setLabel] = useState(role?.label ?? "");
  const [about, setAbout] = useState(role?.description ?? "");
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set((role?.permissions ?? []).filter((permission) => permission !== "self")));

  const save = useMutation({
    mutationFn: () => {
      const permissions = [...chosen].sort();
      return creating
        ? createRole({ name: name.trim(), permissions, label: label.trim() === "" ? null : label.trim(), description: about.trim() === "" ? null : about.trim() })
        : updateRole(role.name, { permissions, label: label.trim() === "" ? null : label.trim(), description: about.trim() === "" ? null : about.trim() });
    },
    onSuccess: (saved) => {
      toast.success(t("access.roles.savedToast", { role: saved.label }));
      void queryClient.invalidateQueries({ queryKey: accessKeys.all });
      onClose();
    },
  });
  const errors = splitErrors(save.error, FIELDS);

  const available = Object.keys(catalog.permissions);
  const groups = groupPermissions(readOnly ? role.permissions : available);
  const places = roleScopes([...chosen], catalog.narrow_permissions, catalog.team_permissions);

  const toggle = (permission: string, on: boolean): void => {
    setChosen((previous) => {
      const next = new Set(previous);
      if (on) next.add(permission);
      else next.delete(permission);
      return next;
    });
  };

  return (
    <Drawer
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      title={creating ? t("access.roles.drawer.createTitle") : readOnly ? t("access.roles.drawer.viewTitle", { role: role.label }) : t("access.roles.drawer.editTitle", { role: role.label })}
      description={readOnly ? t("access.roles.drawer.viewDescription") : t("access.roles.drawer.description")}
      footer={
        readOnly ? (
          <Button onClick={onClose}>{t("access.roles.drawer.cancel")}</Button>
        ) : (
          <>
            <Button disabled={save.isPending} onClick={onClose}>
              {t("access.roles.drawer.cancel")}
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={save.isPending} disabled={chosen.size === 0 || (creating && name.trim() === "")}>
              {creating ? t("access.roles.drawer.create") : t("access.roles.drawer.save")}
            </Button>
          </>
        )
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
          event.preventDefault();
          if (!readOnly && !save.isPending) save.mutate();
        }}
      >
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("access.roles.drawer.errorTitle")} /> : null}
        {!readOnly ? (
          <>
            {creating ? (
              <Field label={t("access.roles.drawer.name")} description={t("access.roles.drawer.nameHelp")} error={errors.fields.name}>
                <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={32} value={name} onValueChange={setName} />
              </Field>
            ) : null}
            <Field label={t("access.roles.drawer.label")} optional description={t("access.roles.drawer.labelHelp")} error={errors.fields.label}>
              <Input maxLength={48} value={label} onValueChange={setLabel} />
            </Field>
            <Field label={t("access.roles.drawer.descriptionField")} optional error={errors.fields.description}>
              <Textarea
                rows={2}
                value={about}
                onChange={(event) => {
                  setAbout(event.target.value);
                }}
              />
            </Field>
          </>
        ) : null}
        <div className="flex flex-col gap-3">
          <div>
            <p className="text-13 font-medium text-fg">{t("access.roles.drawer.permissions")}</p>
            {!readOnly ? <p className="mt-0.5 max-w-measure text-13 text-fg-muted">{t("access.roles.drawer.permissionsHelp")}</p> : null}
          </div>
          {errors.fields.permissions !== undefined ? (
            <Notice tone="error" live>
              {errors.fields.permissions}
            </Notice>
          ) : null}
          {groups.map((group) => (
            <Subsection key={group.area} title={group.area}>
              <div className="flex flex-col gap-2">
                {group.permissions.map((permission) => (
                  <Checkbox
                    key={permission}
                    checked={readOnly ? true : chosen.has(permission)}
                    disabled={readOnly}
                    onCheckedChange={(on) => {
                      toggle(permission, on);
                    }}
                    label={<Mono tone="default">{permission}</Mono>}
                    description={<span translate="no">{catalog.permissions[permission] ?? ""}</span>}
                  />
                ))}
              </div>
            </Subsection>
          ))}
          {!readOnly ? (
            <p className="text-13 text-fg-muted" aria-live="polite">
              {chosen.size === 0
                ? t("access.roles.drawer.noPermissions")
                : t("access.roles.drawer.canBeHeld", { places: places.map((place) => t(`access.scopeShort.${place}`)).join(", ") })}
            </p>
          ) : null}
        </div>
      </form>
    </Drawer>
  );
}
