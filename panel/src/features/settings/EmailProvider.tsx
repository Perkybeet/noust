/**
 * The email service the monitor's mail goes through instead of an SMTP server: Resend, Postmark,
 * Brevo or Amazon SES. Choosing one asks only for what it needs (a key, a sender, for SES a region
 * and an access pair) and replaces the SMTP account; choosing "my SMTP server" goes back to it.
 * Every credential is write-only, like the SMTP password: it is never shown again, and a field left
 * empty keeps the saved one while the service stays the same.
 */

import type { EmailApiSettings } from "../../api/queries/config";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { SecretInput } from "./channelParts";
import { EMAIL_PROVIDERS, PROVIDER_SECRETS, providerFieldsFor, providerSecretSaved } from "./notifications";
import type { EmailProviderChoice, EmailProviderField, EmailProviderForm } from "./notifications";

/** A service's name; "my SMTP server" for none. */
export function providerName(t: T, provider: EmailProviderChoice): string {
  return provider === "" ? t("settings.notifications.email.provider.smtp") : t(`settings.notifications.email.provider.names.${provider}`);
}

const FIELD_TEXT = {
  api_key: { label: "apiKeyLabel", description: "apiKeyDescription", placeholder: "" },
  region: { label: "regionLabel", description: "regionDescription", placeholder: "eu-west-1" },
  access_key_id: { label: "accessKeyIdLabel", description: "accessKeyIdDescription", placeholder: "AKIA…" },
  secret_access_key: { label: "secretAccessKeyLabel", description: "secretAccessKeyDescription", placeholder: "" },
  from_address: { label: "fromLabel", description: "fromDescription", placeholder: "noust@example.com" },
} as const satisfies Record<EmailProviderField, { label: string; description: string; placeholder: string }>;

export function EmailProviderFields({
  form,
  stored,
  set,
  errorOf,
  disabled,
}: {
  form: EmailProviderForm;
  stored: EmailApiSettings;
  set: (change: Partial<EmailProviderForm>) => void;
  errorOf: (name: EmailProviderField | "provider") => string | undefined;
  disabled: boolean;
}) {
  const t = useT();
  const choices: readonly { value: EmailProviderChoice; label: string }[] = [
    { value: "", label: providerName(t, "") },
    ...EMAIL_PROVIDERS.map((value) => ({ value, label: providerName(t, value) })),
  ];
  return (
    <div className="flex min-w-0 flex-col gap-5">
      <Field label={t("settings.notifications.email.provider.label")} nativeLabel={false} description={t("settings.notifications.email.provider.description")} error={errorOf("provider")}>
        <Select<EmailProviderChoice>
          value={form.provider}
          disabled={disabled}
          onValueChange={(next) => {
            set({ provider: next });
          }}
          options={choices}
        />
      </Field>
      {form.provider !== "" ? <p className="text-12 text-pretty text-fg-muted">{t("settings.notifications.email.provider.replacesSmtp", { provider: providerName(t, form.provider) })}</p> : null}
      {providerFieldsFor(form.provider).map((field) => {
        const text = FIELD_TEXT[field];
        const label = t(`settings.notifications.email.provider.${text.label}`);
        return (
          <Field
            key={field}
            label={label}
            description={t(`settings.notifications.email.provider.${text.description}`)}
            error={errorOf(field)}
          >
            {PROVIDER_SECRETS.has(field) ? (
              <SecretInput
                label={label}
                placeholder={text.placeholder}
                value={form[field]}
                configured={providerSecretSaved(stored, field, form.provider)}
                disabled={disabled}
                onChange={(next) => {
                  set({ [field]: next });
                }}
              />
            ) : (
              <Input
                mono
                type={field === "from_address" ? "email" : "text"}
                autoComplete="off"
                spellCheck={false}
                placeholder={text.placeholder}
                value={form[field]}
                disabled={disabled}
                onValueChange={(next: string) => {
                  set({ [field]: next });
                }}
              />
            )}
          </Field>
        );
      })}
    </div>
  );
}
