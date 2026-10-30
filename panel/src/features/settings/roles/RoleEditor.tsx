import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { answerOffers, createRole, rolesChanged, updateRole } from "../../../api/queries/roles";
import type { Role, RolesList } from "../../../api/queries/roles";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { accountKeys } from "../accounts/api";
import { focusFirstInvalid, splitErrors } from "../formErrors";
import { groupPermissions, kindOf, roleDescription, roleLabel } from "./roleModel";

const FIELDS = ["name", "label", "description", "permissions"] as const;

export interface RoleEditorProps {
  /** The role being looked at or changed, or null to define one. */
  role: Role | null;
  catalog: RolesList;
  onClose: () => void;
}

/**
 * What a newer version of Noust offers an edited role: the permissions it ships for it that the role does
 * not hold. Nothing is added in silence (item 96): the operator takes them or leaves them out for good.
 */
function Offers({ role, onDone }: { role: Role; onDone: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const answer = useMutation({
    mutationFn: (taking: boolean) => answerOffers(role.name, taking ? { accept: role.offers ?? [] } : { dismiss: role.offers ?? [] }),
    onSuccess: () => {
      rolesChanged(queryClient);
      onDone();
    },
  });
  return (
    <Notice
      tone="info"
      title={t("roles.offers.title")}
      action={
        <span className="flex flex-wrap gap-2">
          <Button size="sm" variant="primary" loading={answer.isPending && answer.variables} onClick={() => answer.mutate(true)}>
            {t("roles.offers.accept")}
          </Button>
          <Button size="sm" loading={answer.isPending && !answer.variables} onClick={() => answer.mutate(false)}>
            {t("roles.offers.dismiss")}
          </Button>
        </span>
      }
    >
      <div className="flex flex-col gap-2">
        <p className="max-w-measure text-pretty">{t("roles.offers.body", { role: roleLabel(t, role) })}</p>
        <ul className="flex flex-wrap gap-1.5">
          {(role.offers ?? []).map((permission) => (
            <li key={permission}>
              <Mono tone="default">{permission}</Mono>
            </li>
          ))}
        </ul>
        {answer.error !== null ? <ErrorBlock live compact error={answer.error} title={t("roles.drawer.errorTitle")} /> : null}
      </div>
    </Notice>
  );
}

/**
 * Defining a role, changing one, or looking at the owner (item 96). Every role but the owner is edited the
 * same way, the five Noust ships included: changing one makes it yours and it stops following new versions,
 * which the note says before it happens. The permissions are chosen from the ones that exist, each with the
 * server's own sentence on what it allows; the server has the last word on the combination.
 */
export function RoleEditor({ role, catalog, onClose }: RoleEditorProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const creating = role === null;
  const readOnly = role !== null && !role.can_edit;
  const [name, setName] = useState(role?.name ?? "");
  const [label, setLabel] = useState(role === null ? "" : roleLabel(t, role));
  const [about, setAbout] = useState(role === null ? "" : roleDescription(t, role));
  const [tried, setTried] = useState(false);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set((role?.permissions ?? []).filter((permission) => permission !== "self")));

  const save = useMutation({
    mutationFn: () => {
      const permissions = [...chosen].sort();
      const body = { permissions, label: label.trim() === "" ? null : label.trim(), description: about.trim() === "" ? null : about.trim() };
      return creating ? createRole({ name: name.trim(), ...body }) : updateRole(role.name, body);
    },
    onSuccess: () => {
      rolesChanged(queryClient);
      void queryClient.invalidateQueries({ queryKey: accountKeys.list });
      onClose();
    },
  });
  const errors = splitErrors(save.error, FIELDS);

  const groups = groupPermissions(readOnly ? role.permissions : Object.keys(catalog.permissions));
  const toggle = (permission: string, on: boolean): void => {
    setChosen((previous) => {
      const next = new Set(previous);
      if (on) next.add(permission);
      else next.delete(permission);
      return next;
    });
  };
  const kind = role === null ? null : kindOf(role);

  return (
    <Drawer
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      title={creating ? t("roles.drawer.createTitle") : readOnly ? t("roles.drawer.viewTitle", { role: roleLabel(t, role) }) : t("roles.drawer.editTitle", { role: roleLabel(t, role) })}
      description={creating ? t("roles.drawer.createDescription") : readOnly ? t("roles.drawer.ownerNote") : t("roles.drawer.editDescription")}
      footer={
        readOnly ? (
          <Button onClick={onClose}>{t("common.words.close")}</Button>
        ) : (
          <>
            <Button disabled={save.isPending} onClick={onClose}>
              {t("common.words.cancel")}
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
              {creating ? t("roles.drawer.create") : t("roles.drawer.save")}
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
          if (readOnly || save.isPending) return;
          // The button is never off for a missing value: it says what is missing, where, and goes there.
          if (chosen.size === 0 || (creating && name.trim() === "")) {
            setTried(true);
            focusFirstInvalid(formId);
            return;
          }
          save.mutate();
        }}
      >
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("roles.drawer.errorTitle")} /> : null}
        {role !== null && !readOnly && (role.offers ?? []).length > 0 ? <Offers role={role} onDone={onClose} /> : null}
        {kind === "follows" ? <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("roles.drawer.seededNote")}</p> : null}
        {!readOnly ? (
          <>
            {creating ? (
              <Field label={t("common.words.name")} description={t("roles.drawer.nameHelp")} error={errors.fields.name ?? (tried && name.trim() === "" ? t("settings.shared.required") : undefined)}>
                <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={40} value={name} onValueChange={setName} />
              </Field>
            ) : null}
            <Field label={t("roles.drawer.label")} optional description={t("roles.drawer.labelHelp")} error={errors.fields.label}>
              <Input maxLength={48} value={label} onValueChange={setLabel} />
            </Field>
            <Field label={t("roles.drawer.descriptionField")} optional error={errors.fields.description}>
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
            <p className="text-13 font-medium text-fg">{t("roles.drawer.permissions")}</p>
            {!readOnly ? <p className="mt-0.5 max-w-measure text-13 text-fg-muted">{t("roles.drawer.permissionsHelp")}</p> : null}
          </div>
          {errors.fields.permissions !== undefined || (tried && chosen.size === 0) ? (
            <InlineMessage tone="error" live>
              {errors.fields.permissions ?? t("roles.drawer.permissionsRequired")}
            </InlineMessage>
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
        </div>
        {role !== null ? (
          <div className="flex flex-col gap-1.5">
            <p className="text-13 font-medium text-fg">{t("roles.drawer.holders")}</p>
            {(role.holders ?? []).length === 0 ? (
              <p className="text-13 text-fg-muted">{t("roles.drawer.nobody")}</p>
            ) : (
              <ul className="flex flex-wrap gap-1.5">
                {(role.holders ?? []).map((holder) => (
                  <li key={holder}>
                    <Mono tone="default">{holder}</Mono>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : null}
      </form>
    </Drawer>
  );
}
