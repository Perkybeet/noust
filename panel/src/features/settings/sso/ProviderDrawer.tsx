import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { saveProvider, ssoAdminKeys } from "../../../api/queries/ssoAdmin";
import type { SsoProviderInfo } from "../../../api/queries/ssoAdmin";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { splitErrors } from "../formErrors";
import { ADMISSION_FOLDED, AdmissionFields } from "./AdmissionFields";
import { NEW_DRAFT, draftOf, settingsOf } from "./providerForm";
import type { ProviderDraft } from "./providerForm";

const FIELDS = [
  "label",
  "issuer",
  "client_id",
  "client_secret",
  "scopes",
  "provisioning",
  "default_role",
  "default_team",
  "default_team_role",
  "allowed_domains",
  "require_amr",
  "groups_claim",
  "group_mappings",
  "clock_skew_seconds",
  "redirect_uri",
] as const;

export interface ProviderDrawerProps {
  /** The provider being changed, or null to add one. */
  provider: SsoProviderInfo | null;
  /** The address every provider is registered with, for the field that overrides it. */
  redirectUri: string;
  onClose: () => void;
}

/**
 * Adding or changing an identity provider. The first fields are what every provider needs; who
 * may come in, groups and the rarely changed options are folded under their own buttons. What the
 * server refuses is shown beside the field it is about, and a refusal about a folded field is
 * said above the form too, so it is not missed behind a closed fold.
 */
export function ProviderDrawer({ provider, redirectUri, onClose }: ProviderDrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const creating = provider === null;
  const [draft, setDraft] = useState<ProviderDraft>(provider === null ? NEW_DRAFT : draftOf(provider));
  const [badLine, setBadLine] = useState<number | null>(null);

  const set = <K extends keyof ProviderDraft>(key: K, value: ProviderDraft[K]): void => {
    setDraft((previous) => ({ ...previous, [key]: value }));
  };

  const save = useMutation({
    mutationFn: async () => {
      const built = settingsOf(draft);
      if (built.settings === null) {
        setBadLine(built.badLine);
        return null;
      }
      setBadLine(null);
      return saveProvider(draft.name.trim(), built.settings);
    },
    onSuccess: (saved) => {
      if (saved === null) return;
      toast.success(t("ssoAdmin.providers.savedToast", { name: saved.name }));
      void queryClient.invalidateQueries({ queryKey: ssoAdminKeys.all });
      onClose();
    },
  });
  const errors = splitErrors(save.error, FIELDS);
  const folded: readonly (typeof FIELDS)[number][] = [
    ...ADMISSION_FOLDED,
    "scopes",
    "clock_skew_seconds",
    "redirect_uri",
  ];
  const hiddenMessage = folded.map((name) => errors.fields[name]).find((message) => message !== undefined);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!save.isPending) save.mutate();
  };
  const ready = draft.issuer.trim() !== "" && draft.clientId.trim() !== "" && (!creating || draft.name.trim() !== "");

  return (
    <Drawer
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      title={creating ? t("ssoAdmin.form.createTitle") : t("ssoAdmin.form.editTitle", { name: provider.name })}
      description={t("ssoAdmin.form.description")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("ssoAdmin.form.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending} disabled={!ready}>
            {creating ? t("ssoAdmin.form.create") : t("ssoAdmin.form.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("ssoAdmin.form.errorTitle")} /> : null}
        {hiddenMessage !== undefined ? (
          <Notice tone="error" live title={t("ssoAdmin.form.errorTitle")}>
            {hiddenMessage}
          </Notice>
        ) : null}
        {badLine !== null ? (
          <Notice tone="error" live title={t("ssoAdmin.form.errorTitle")}>
            {t("ssoAdmin.form.mappingsLine", { line: badLine })}
          </Notice>
        ) : null}

        {creating ? (
          <Field label={t("ssoAdmin.form.fields.name")} description={t("ssoAdmin.form.fields.nameHelp")}>
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
        <Field label={t("ssoAdmin.form.fields.label")} optional description={t("ssoAdmin.form.fields.labelHelp")} error={errors.fields.label}>
          <Input
            maxLength={40}
            value={draft.label}
            onValueChange={(value: string) => {
              set("label", value);
            }}
          />
        </Field>
        <Field label={t("ssoAdmin.form.fields.issuer")} description={t("ssoAdmin.form.fields.issuerHelp")} error={errors.fields.issuer}>
          <Input
            mono
            type="url"
            autoComplete="off"
            spellCheck={false}
            placeholder="https://sso.example.com/realms/acme"
            value={draft.issuer}
            onValueChange={(value: string) => {
              set("issuer", value);
            }}
          />
        </Field>
        <Field label={t("ssoAdmin.form.fields.clientId")} error={errors.fields.client_id}>
          <Input
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            value={draft.clientId}
            onValueChange={(value: string) => {
              set("clientId", value);
            }}
          />
        </Field>
        <Field
          label={t("ssoAdmin.form.fields.clientSecret")}
          optional
          description={creating || !provider.client_secret_set ? t("ssoAdmin.form.fields.clientSecretHelp") : t("ssoAdmin.form.fields.clientSecretKept")}
          error={errors.fields.client_secret}
        >
          <Input
            mono
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            value={draft.clientSecret}
            onValueChange={(value: string) => {
              set("clientSecret", value);
            }}
          />
        </Field>
        {!creating && provider.client_secret_set ? (
          <Checkbox
            label={t("ssoAdmin.form.fields.clearSecret")}
            checked={draft.clearSecret}
            onCheckedChange={(value) => {
              set("clearSecret", value);
            }}
          />
        ) : null}
        <Checkbox
          label={t("ssoAdmin.form.fields.enabled")}
          description={t("ssoAdmin.form.fields.enabledHelp")}
          checked={draft.enabled}
          onCheckedChange={(value) => {
            set("enabled", value);
          }}
        />

        <AdmissionFields
          draft={draft}
          onChange={(patch) => {
            setDraft((previous) => ({ ...previous, ...patch }));
          }}
          errors={errors.fields}
          creating={creating}
          parts={{ provisioning: true, emailTrust: true, methods: true, claim: true }}
        />

        <Disclosure label={t("ssoAdmin.form.groups.more")}>
          <div className="flex flex-col gap-5">
            <Field label={t("ssoAdmin.form.fields.scopes")} description={t("ssoAdmin.form.fields.scopesHelp")} error={errors.fields.scopes}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                value={draft.scopes}
                onValueChange={(value: string) => {
                  set("scopes", value);
                }}
              />
            </Field>
            <Field label={t("ssoAdmin.form.fields.clockSkew")} description={t("ssoAdmin.form.fields.clockSkewHelp")} error={errors.fields.clock_skew_seconds}>
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
            <Field
              label={t("ssoAdmin.form.fields.redirectUri")}
              optional
              description={t("ssoAdmin.form.fields.redirectUriHelp", { address: provider?.redirect_uri ?? redirectUri })}
              error={errors.fields.redirect_uri}
            >
              <Input
                mono
                type="url"
                autoComplete="off"
                spellCheck={false}
                value={draft.redirectUri}
                onValueChange={(value: string) => {
                  set("redirectUri", value);
                }}
              />
            </Field>
          </div>
        </Disclosure>
      </form>
    </Drawer>
  );
}
