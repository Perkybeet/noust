import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { ldapKeys, saveDirectory } from "../../../api/queries/ldap";
import type { Directory } from "../../../api/queries/ldap";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { splitErrors } from "../formErrors";
import { ADMISSION_FOLDED, AdmissionFields } from "./AdmissionFields";
import { NEW_DIRECTORY_DRAFT, TLS_MODES, directoryDraftOf, directorySettingsOf } from "./directoryForm";
import type { DirectoryDraft, TlsMode } from "./directoryForm";

const FIELDS = [
  "label",
  "host",
  "port",
  "tls",
  "ca_file",
  "bind_dn",
  "bind_password",
  "user_base",
  "user_filter",
  "subject_attribute",
  "email_attribute",
  "name_attribute",
  "username_attribute",
  "groups_attribute",
  "group_base",
  "group_filter",
  "timeout_seconds",
  ...ADMISSION_FOLDED,
] as const;

/** The fields behind a fold: a refusal about one is said above the form too, so it is not missed. */
const FOLDED: readonly (typeof FIELDS)[number][] = [
  "ca_file",
  "bind_dn",
  "bind_password",
  "user_base",
  "user_filter",
  "subject_attribute",
  "email_attribute",
  "name_attribute",
  "username_attribute",
  "groups_attribute",
  "group_base",
  "group_filter",
  "timeout_seconds",
  ...ADMISSION_FOLDED,
];

/** The port a mode is usually on, for a form that has not had its port typed over. */
const DEFAULT_PORT: Record<TlsMode, string> = { ldaps: "636", starttls: "389", none: "389" };

export interface DirectoryDrawerProps {
  /** The directory being changed, or null to add one. */
  directory: Directory | null;
  onClose: () => void;
}

/**
 * Adding or changing a directory. The first fields are what every directory needs; the service
 * account, how people are found, where groups are read and who may come in are folded under their
 * own buttons. What the server refuses is shown beside the field it is about.
 */
export function DirectoryDrawer({ directory, onClose }: DirectoryDrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const creating = directory === null;
  const [draft, setDraft] = useState<DirectoryDraft>(directory === null ? NEW_DIRECTORY_DRAFT : directoryDraftOf(directory));
  const [badLine, setBadLine] = useState<number | null>(null);

  const set = <K extends keyof DirectoryDraft>(key: K, value: DirectoryDraft[K]): void => {
    setDraft((previous) => ({ ...previous, [key]: value }));
  };

  const save = useMutation({
    mutationFn: async () => {
      const built = directorySettingsOf(draft);
      if (built.settings === null) {
        setBadLine(built.badLine);
        return null;
      }
      setBadLine(null);
      return saveDirectory(draft.name.trim(), built.settings);
    },
    onSuccess: (saved) => {
      if (saved === null) return;
      toast.success(t("ldapAdmin.savedToast", { name: saved.name }));
      void queryClient.invalidateQueries({ queryKey: ldapKeys.all });
      onClose();
    },
  });
  const errors = splitErrors(save.error, FIELDS);
  const hiddenMessage = FOLDED.map((name) => errors.fields[name]).find((message) => message !== undefined);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!save.isPending) save.mutate();
  };
  const ready = draft.host.trim() !== "" && draft.userBase.trim() !== "" && (!creating || draft.name.trim() !== "");
  const tlsOptions: { value: TlsMode; label: string }[] = [
    { value: "ldaps", label: t("ldapAdmin.form.tlsLdaps") },
    { value: "starttls", label: t("ldapAdmin.form.tlsStarttls") },
    { value: "none", label: t("ldapAdmin.form.tlsNone") },
  ];

  return (
    <Drawer
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      title={creating ? t("ldapAdmin.form.createTitle") : t("ldapAdmin.form.editTitle", { name: directory.name })}
      description={t("ldapAdmin.form.description")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("ldapAdmin.form.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending} disabled={!ready}>
            {creating ? t("ldapAdmin.form.create") : t("ldapAdmin.form.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("ldapAdmin.form.errorTitle")} /> : null}
        {hiddenMessage !== undefined ? (
          <Notice tone="error" live title={t("ldapAdmin.form.errorTitle")}>
            {hiddenMessage}
          </Notice>
        ) : null}
        {badLine !== null ? (
          <Notice tone="error" live title={t("ldapAdmin.form.errorTitle")}>
            {t("ldapAdmin.form.mappingsLine", { line: badLine })}
          </Notice>
        ) : null}

        {creating ? (
          <Field label={t("ldapAdmin.form.name")} description={t("ldapAdmin.form.nameHelp")}>
            <Input
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              maxLength={32}
              value={draft.name}
              onValueChange={(value: string) => {
                set("name", value);
              }}
            />
          </Field>
        ) : null}
        <Field label={t("ldapAdmin.form.label")} optional description={t("ldapAdmin.form.labelHelp")} error={errors.fields.label}>
          <Input
            maxLength={40}
            value={draft.label}
            onValueChange={(value: string) => {
              set("label", value);
            }}
          />
        </Field>
        <Field label={t("ldapAdmin.form.host")} description={t("ldapAdmin.form.hostHelp")} error={errors.fields.host}>
          <Input
            mono
            autoComplete="off"
            spellCheck={false}
            placeholder="ldap.acme.test"
            value={draft.host}
            onValueChange={(value: string) => {
              set("host", value);
            }}
          />
        </Field>
        <Field label={t("ldapAdmin.form.tls")} nativeLabel={false} error={errors.fields.tls}>
          <Select
            options={tlsOptions}
            value={draft.tls}
            onValueChange={(value) => {
              const mode = TLS_MODES.find((known) => known === value) ?? "ldaps";
              // The usual port follows the mode, unless a port of another kind was typed.
              const usual = Object.values(DEFAULT_PORT).includes(draft.port);
              setDraft((previous) => ({ ...previous, tls: mode, ...(usual ? { port: DEFAULT_PORT[mode] } : {}) }));
            }}
          />
        </Field>
        <Field label={t("ldapAdmin.form.port")} error={errors.fields.port}>
          <Input
            mono
            inputMode="numeric"
            autoComplete="off"
            className="w-28"
            value={draft.port}
            onValueChange={(value: string) => {
              set("port", value);
            }}
          />
        </Field>
        <Checkbox
          label={t("ldapAdmin.form.enabled")}
          checked={draft.enabled}
          onCheckedChange={(value) => {
            set("enabled", value);
          }}
        />

        <Disclosure label={t("ldapAdmin.form.service.title")} defaultOpen={!creating}>
          <div className="flex flex-col gap-5">
            <Field label={t("ldapAdmin.form.service.bindDn")} optional description={t("ldapAdmin.form.service.bindDnHelp")} error={errors.fields.bind_dn}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                placeholder="cn=noust,ou=system,dc=acme,dc=test"
                value={draft.bindDn}
                onValueChange={(value: string) => {
                  set("bindDn", value);
                }}
              />
            </Field>
            <Field
              label={t("ldapAdmin.form.service.password")}
              optional
              description={creating || !directory.bind_password_set ? t("ldapAdmin.form.service.passwordHelp") : t("ldapAdmin.form.service.passwordKept")}
              error={errors.fields.bind_password}
            >
              <Input
                mono
                type="password"
                autoComplete="new-password"
                spellCheck={false}
                value={draft.bindPassword}
                onValueChange={(value: string) => {
                  set("bindPassword", value);
                }}
              />
            </Field>
            {!creating && directory.bind_password_set ? (
              <Checkbox
                label={t("ldapAdmin.form.service.clear")}
                checked={draft.clearBindPassword}
                onCheckedChange={(value) => {
                  set("clearBindPassword", value);
                }}
              />
            ) : null}
            <Field label={t("ldapAdmin.form.caFile")} optional description={t("ldapAdmin.form.caFileHelp")} error={errors.fields.ca_file}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                placeholder="/etc/ssl/certs/acme-ca.pem"
                value={draft.caFile}
                onValueChange={(value: string) => {
                  set("caFile", value);
                }}
              />
            </Field>
          </div>
        </Disclosure>

        <Disclosure label={t("ldapAdmin.form.people.title")} defaultOpen={creating}>
          <div className="flex flex-col gap-5">
            <Field label={t("ldapAdmin.form.people.userBase")} description={t("ldapAdmin.form.people.userBaseHelp")} error={errors.fields.user_base}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                placeholder="ou=people,dc=acme,dc=test"
                value={draft.userBase}
                onValueChange={(value: string) => {
                  set("userBase", value);
                }}
              />
            </Field>
            <Field label={t("ldapAdmin.form.people.userFilter")} description={t("ldapAdmin.form.people.userFilterHelp", { placeholder: "{username}" })} error={errors.fields.user_filter}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                value={draft.userFilter}
                onValueChange={(value: string) => {
                  set("userFilter", value);
                }}
              />
            </Field>
            <Field label={t("ldapAdmin.form.people.subject")} description={t("ldapAdmin.form.people.subjectHelp")} error={errors.fields.subject_attribute}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                value={draft.subjectAttribute}
                onValueChange={(value: string) => {
                  set("subjectAttribute", value);
                }}
              />
            </Field>
            <Field label={t("ldapAdmin.form.people.email")} error={errors.fields.email_attribute}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                value={draft.emailAttribute}
                onValueChange={(value: string) => {
                  set("emailAttribute", value);
                }}
              />
            </Field>
            <Field label={t("ldapAdmin.form.people.displayName")} error={errors.fields.name_attribute}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                value={draft.nameAttribute}
                onValueChange={(value: string) => {
                  set("nameAttribute", value);
                }}
              />
            </Field>
            <Field label={t("ldapAdmin.form.people.username")} error={errors.fields.username_attribute}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                value={draft.usernameAttribute}
                onValueChange={(value: string) => {
                  set("usernameAttribute", value);
                }}
              />
            </Field>
            <Field label={t("ldapAdmin.form.people.timeout")} error={errors.fields.timeout_seconds}>
              <Input
                mono
                inputMode="numeric"
                autoComplete="off"
                className="w-28"
                value={draft.timeout}
                onValueChange={(value: string) => {
                  set("timeout", value);
                }}
              />
            </Field>
          </div>
        </Disclosure>

        <Disclosure label={t("ldapAdmin.form.groups.title")}>
          <div className="flex flex-col gap-5">
            <Field label={t("ldapAdmin.form.groups.attribute")} description={t("ldapAdmin.form.groups.attributeHelp")} error={errors.fields.groups_attribute}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                value={draft.groupsAttribute}
                onValueChange={(value: string) => {
                  set("groupsAttribute", value);
                }}
              />
            </Field>
            <Field label={t("ldapAdmin.form.groups.base")} optional description={t("ldapAdmin.form.groups.baseHelp")} error={errors.fields.group_base}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                placeholder="ou=groups,dc=acme,dc=test"
                value={draft.groupBase}
                onValueChange={(value: string) => {
                  set("groupBase", value);
                }}
              />
            </Field>
            <Field label={t("ldapAdmin.form.groups.filter")} optional description={t("ldapAdmin.form.groups.filterHelp", { placeholder: "{dn}", example: "(&(objectClass=groupOfNames)(member={dn}))" })} error={errors.fields.group_filter}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                value={draft.groupFilter}
                onValueChange={(value: string) => {
                  set("groupFilter", value);
                }}
              />
            </Field>
          </div>
        </Disclosure>

        <AdmissionFields
          draft={draft}
          onChange={(patch) => {
            setDraft((previous) => ({ ...previous, ...patch }));
          }}
          errors={errors.fields}
          creating={creating}
          parts={{ provisioning: true, emailTrust: true, methods: false, claim: false }}
        />
      </form>
    </Drawer>
  );
}
