import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { samlKeys, saveSamlProvider } from "../../../api/queries/saml";
import type { SamlProvider } from "../../../api/queries/saml";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { CopyValue } from "../../../components/ui/CopyValue";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Input } from "../../../components/ui/Input";
import { Textarea } from "../../../components/ui/Textarea";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { focusFirstInvalid, splitErrors } from "../formErrors";
import { ADMISSION_FOLDED, AdmissionFields } from "./AdmissionFields";
import { NEW_SAML_DRAFT, samlDraftOf, samlSettingsOf, splitCertificates } from "./samlForm";
import type { SamlDraft } from "./samlForm";

const FIELDS = [
  "label",
  "idp_metadata",
  "idp_entity_id",
  "sso_url",
  "certificates",
  "sp_entity_id",
  "subject_attribute",
  "email_attribute",
  "name_attribute",
  "username_attribute",
  "groups_attribute",
  "clock_skew_seconds",
  "require_amr",
  ...ADMISSION_FOLDED,
] as const;

/** The fields behind a fold: a refusal about one is said above the form too, so it is not missed. */
const FOLDED: readonly (typeof FIELDS)[number][] = [
  "sp_entity_id",
  "subject_attribute",
  "email_attribute",
  "name_attribute",
  "username_attribute",
  "groups_attribute",
  "clock_skew_seconds",
  "require_amr",
  ...ADMISSION_FOLDED,
];

export interface SamlDrawerProps {
  /** The provider being changed, or null to add one. */
  provider: SamlProvider | null;
  onClose: () => void;
}

/**
 * Adding or changing a SAML provider. The first fields are what every provider needs: its
 * metadata, or its entity id, sign-in address and certificates. How this server presents itself,
 * how people are found and who may come in are folded under their own buttons. What the server
 * refuses is shown beside the field it is about.
 */
export function SamlDrawer({ provider, onClose }: SamlDrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const creating = provider === null;
  const [draft, setDraft] = useState<SamlDraft>(provider === null ? NEW_SAML_DRAFT : samlDraftOf(provider));
  const [badLine, setBadLine] = useState<number | null>(null);

  const set = <K extends keyof SamlDraft>(key: K, value: SamlDraft[K]): void => {
    setDraft((previous) => ({ ...previous, [key]: value }));
  };

  const save = useMutation({
    mutationFn: async () => {
      const built = samlSettingsOf(draft);
      if (built.settings === null) {
        setBadLine(built.badLine);
        return null;
      }
      setBadLine(null);
      return saveSamlProvider(draft.name.trim(), built.settings);
    },
    onSuccess: (saved) => {
      if (saved === null) return;
      toast.success(t("samlAdmin.savedToast", { name: saved.name }));
      void queryClient.invalidateQueries({ queryKey: samlKeys.all });
      onClose();
    },
  });
  const [tried, setTried] = useState(false);
  const required = t("settings.shared.required");
  const errors = splitErrors(save.error, FIELDS);
  const hiddenMessage = FOLDED.map((name) => errors.fields[name]).find((message) => message !== undefined);

  // A new provider needs its metadata, or all three of its settings; an old one has them already.
  const described = draft.metadata.trim() !== "" || (draft.entityId.trim() !== "" && draft.ssoUrl.trim() !== "" && splitCertificates(draft.certificates).length > 0);
  const ready = creating ? draft.name.trim() !== "" && described : true;
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (save.isPending) return;
    // The button is never off for a missing value: it says what is missing, where, and goes there.
    if (!ready) {
      setTried(true);
      focusFirstInvalid(formId);
      return;
    }
    save.mutate();
  };

  return (
    <Drawer
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      title={creating ? t("samlAdmin.form.createTitle") : t("common.words.editNamed", { name: provider.name })}
      description={t("samlAdmin.form.description")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {creating ? t("samlAdmin.form.create") : t("samlAdmin.form.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("samlAdmin.form.errorTitle")} /> : null}
        {hiddenMessage !== undefined ? (
          <InlineMessage tone="error" live>
            <span className="font-medium">{t("samlAdmin.form.errorTitle")}</span> {hiddenMessage}
          </InlineMessage>
        ) : null}
        {badLine !== null ? (
          <InlineMessage tone="error" live>
            <span className="font-medium">{t("samlAdmin.form.errorTitle")}</span> {t("samlAdmin.form.mappingsLine", { line: badLine })}
          </InlineMessage>
        ) : null}

        {provider !== null ? (
          <section aria-label={t("samlAdmin.form.register.title")} className="flex flex-col gap-3 rounded-card border border-border p-4">
            <p className="text-13 font-medium text-fg">{t("samlAdmin.form.register.title")}</p>
            <p className="text-13 text-fg-muted">{t("samlAdmin.form.register.description")}</p>
            <CopyValue value={provider.entity_id} label={t("samlAdmin.form.register.entityId")} name={t("samlAdmin.form.register.entityId")} />
            <CopyValue value={provider.acs_url} label={t("samlAdmin.form.register.acs")} name={t("samlAdmin.form.register.acs")} />
            <CopyValue value={provider.metadata_url} label={t("samlAdmin.form.register.metadata")} name={t("samlAdmin.form.register.metadata")} />
          </section>
        ) : null}

        {creating ? (
          <Field label={t("common.words.name")} description={t("samlAdmin.form.nameHelp")} error={tried && draft.name.trim() === "" ? required : undefined}>
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
        <Field label={t("samlAdmin.form.label")} optional description={t("samlAdmin.form.labelHelp")} error={errors.fields.label}>
          <Input
            maxLength={40}
            value={draft.label}
            onValueChange={(value: string) => {
              set("label", value);
            }}
          />
        </Field>
        <Checkbox
          label={t("samlAdmin.form.enabled")}
          checked={draft.enabled}
          onCheckedChange={(value) => {
            set("enabled", value);
          }}
        />

        <Disclosure label={t("samlAdmin.form.idp.title")} defaultOpen>
          <div className="flex flex-col gap-5">
            <Field label={t("samlAdmin.form.idp.metadata")} optional description={t("samlAdmin.form.idp.metadataHelp")} error={errors.fields.idp_metadata ?? (tried && creating && !described ? t("samlAdmin.form.idp.needed") : undefined)}>
              <Textarea
                mono
                rows={4}
                spellCheck={false}
                placeholder="<md:EntityDescriptor ...>"
                value={draft.metadata}
                onChange={(event) => {
                  set("metadata", event.target.value);
                }}
              />
            </Field>
            <Field label={t("samlAdmin.form.idp.entityId")} optional={creating} description={t("samlAdmin.form.idp.entityIdHelp")} error={errors.fields.idp_entity_id}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                placeholder="https://idp.acme.test/metadata"
                value={draft.entityId}
                onValueChange={(value: string) => {
                  set("entityId", value);
                }}
              />
            </Field>
            <Field label={t("samlAdmin.form.idp.ssoUrl")} optional={creating} description={t("samlAdmin.form.idp.ssoUrlHelp")} error={errors.fields.sso_url}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                placeholder="https://idp.acme.test/sso"
                value={draft.ssoUrl}
                onValueChange={(value: string) => {
                  set("ssoUrl", value);
                }}
              />
            </Field>
            <Field label={t("samlAdmin.form.idp.certificates")} optional={creating} description={t("samlAdmin.form.idp.certificatesHelp")} error={errors.fields.certificates}>
              <Textarea
                mono
                rows={6}
                spellCheck={false}
                placeholder={"-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----"}
                value={draft.certificates}
                onChange={(event) => {
                  set("certificates", event.target.value);
                }}
              />
            </Field>
          </div>
        </Disclosure>

        <Disclosure label={t("samlAdmin.form.server.title")}>
          <div className="flex flex-col gap-5">
            <Field label={t("samlAdmin.form.server.entityId")} optional description={t("samlAdmin.form.server.entityIdHelp")} error={errors.fields.sp_entity_id}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                value={draft.spEntityId}
                onValueChange={(value: string) => {
                  set("spEntityId", value);
                }}
              />
            </Field>
          </div>
        </Disclosure>

        <Disclosure label={t("samlAdmin.form.people.title")}>
          <div className="flex flex-col gap-5">
            <Field label={t("samlAdmin.form.people.subject")} optional description={t("samlAdmin.form.people.subjectHelp")} error={errors.fields.subject_attribute}>
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
            <Field label={t("samlAdmin.form.people.email")} optional error={errors.fields.email_attribute}>
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
            <Field label={t("samlAdmin.form.people.displayName")} optional error={errors.fields.name_attribute}>
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
            <Field label={t("samlAdmin.form.people.username")} optional error={errors.fields.username_attribute}>
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
            <Field label={t("samlAdmin.form.people.groups")} optional description={t("samlAdmin.form.people.groupsHelp")} error={errors.fields.groups_attribute}>
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
            <Field label={t("samlAdmin.form.contexts")} optional description={t("samlAdmin.form.contextsHelp")} error={errors.fields.require_amr}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                value={draft.requireAmr}
                onValueChange={(value: string) => {
                  set("requireAmr", value);
                }}
              />
            </Field>
            <Field label={t("samlAdmin.form.people.skew")} error={errors.fields.clock_skew_seconds}>
              <Input
                mono
                inputMode="numeric"
                autoComplete="off"
                className="w-28"
                value={draft.clockSkew}
                onValueChange={(value: string) => {
                  set("clockSkew", value);
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
