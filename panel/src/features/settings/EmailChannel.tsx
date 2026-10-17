import { useMutation, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { ReactNode } from "react";

import { emailApiQuery, saveEmailApiSettings, saveSmtpSettings, smtpSettingsQuery } from "../../api/queries/config";
import type { SmtpSettings } from "../../api/queries/config";
import { saveNotificationChannel } from "../../api/queries/notifications";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { ChannelDrawer, ChannelRow, SecretInput, useChannelTest, useRefreshConfig } from "./channelParts";
import { EmailProviderFields, providerName } from "./EmailProvider";
import { splitConfigErrors } from "./formErrors";
import {
  PROVIDER_CONFIG_KEYS,
  SMTP_CONFIG_KEYS,
  SMTP_FIELDS,
  looksLikeEmail,
  portForSecurity,
  providerBody,
  providerFormDirty,
  providerFormFrom,
  refusedRecipients,
  smtpBody,
  smtpFormDirty,
  smtpFormFrom,
  smtpSecurityOptions,
  splitAddresses,
} from "./notifications";
import type { EmailProviderField, EmailProviderForm, SmtpField, SmtpForm, SmtpSecurity } from "./notifications";
import { FieldsSkeleton, FormFailure } from "./SettingsForm";

/**
 * The addresses email goes to: each one on its own row with a way to remove it, and a box that
 * adds one or several. The shape of each address is checked as it is added; the server still
 * rules on the list, and an address it refuses is marked where it stands.
 */
function RecipientsEditor({
  recipients,
  onChange,
  error,
  disabled,
}: {
  recipients: readonly string[];
  onChange: (next: readonly string[]) => void;
  /** The server's refusal of the list, verbatim. */
  error: string | undefined;
  disabled: boolean;
}) {
  const t = useT();
  const [typed, setTyped] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const legendId = useId();
  const refused = refusedRecipients(error, recipients);

  const add = (): void => {
    const addresses = splitAddresses(typed);
    if (addresses.length === 0) return;
    const invalid = addresses.filter((address) => !looksLikeEmail(address));
    if (invalid.length > 0) {
      setProblem(
        invalid.length === 1
          ? t("settings.notifications.email.recipients.invalidOne", { address: invalid[0] ?? "" })
          : t("settings.notifications.email.recipients.invalidMany", { addresses: invalid.join(", ") }),
      );
      return;
    }
    const known = new Set(recipients.map((address) => address.toLowerCase()));
    const fresh = addresses.filter((address) => {
      const key = address.toLowerCase();
      if (known.has(key)) return false;
      known.add(key);
      return true;
    });
    onChange([...recipients, ...fresh]);
    setTyped("");
    setProblem(null);
  };

  return (
    <div role="group" aria-labelledby={legendId} className="flex min-w-0 flex-col gap-2">
      <p id={legendId} className="text-13 font-medium text-fg">
        {t("settings.notifications.email.recipients.legend")}
      </p>
      {recipients.length > 0 ? (
        <ul aria-label={t("settings.notifications.email.recipients.listLabel")} className="flex min-w-0 flex-col divide-y divide-border rounded-control border border-border bg-bg-sunken">
          {recipients.map((address) => {
            const bad = refused.has(address);
            return (
              <li key={address} className="flex min-h-9 min-w-0 items-center gap-2 py-1 pr-1 pl-3">
                {bad ? <StatusGlyph state="failed" size={10} className="text-fail" /> : null}
                <Mono truncate title={address} className="min-w-0 flex-1 text-12">
                  {address}
                </Mono>
                {bad ? <span className="shrink-0 text-12 text-fail">{t("settings.notifications.email.recipients.refused")}</span> : null}
                <IconButton
                  size="sm"
                  label={t("settings.notifications.email.recipients.removeLabel", { address })}
                  icon={<ICONS.dismiss />}
                  disabled={disabled}
                  onClick={() => {
                    onChange(recipients.filter((other) => other !== address));
                  }}
                />
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-13 text-fg-muted">{t("settings.notifications.email.recipients.empty")}</p>
      )}
      <Field
        label={t("settings.notifications.email.recipients.addLabel")}
        error={problem ?? error}
        description={t("settings.notifications.email.recipients.addDescription")}
        action={
          <Button icon={<ICONS.add aria-hidden="true" />} disabled={disabled || typed.trim() === ""} onClick={add}>
            {t("settings.notifications.email.recipients.add")}
          </Button>
        }
      >
        <Input
          mono
          type="email"
          inputMode="email"
          autoComplete="off"
          spellCheck={false}
          placeholder="ops@example.com"
          value={typed}
          disabled={disabled}
          onValueChange={(next: string) => {
            setTyped(next);
            setProblem(null);
          }}
          onKeyDown={(event) => {
            // Enter adds the address instead of submitting the whole form half-edited.
            if (event.key === "Enter") {
              event.preventDefault();
              add();
            }
          }}
        />
      </Field>
    </div>
  );
}

/** The SMTP account's fields, over what the server holds. */
function EmailFields({
  form,
  stored,
  enabled,
  onEnabled,
  set,
  setSecurity,
  errorOf,
  disabled,
  viaService,
  provider,
}: {
  form: SmtpForm;
  stored: SmtpSettings;
  enabled: boolean;
  onEnabled: (next: boolean) => void;
  set: <K extends keyof SmtpForm>(name: K, value: SmtpForm[K]) => void;
  setSecurity: (next: SmtpSecurity) => void;
  errorOf: (name: SmtpField) => string | undefined;
  disabled: boolean;
  /** An email service stands in for the SMTP server: its fields are not shown. */
  viaService: boolean;
  /** The service's own fields, drawn between the switch and the account. */
  provider: ReactNode;
}) {
  const t = useT();
  const options = smtpSecurityOptions(t);
  const security = options.find((option) => option.value === form.security) ?? options[0];
  return (
    <>
      <Checkbox label={t("settings.notifications.email.sendByEmail")} checked={enabled} disabled={disabled} onCheckedChange={onEnabled} />
      {provider}
      {viaService ? null : (
        <SmtpAccountFields form={form} stored={stored} set={set} setSecurity={setSecurity} errorOf={errorOf} disabled={disabled} options={options} securityDescription={security?.description} />
      )}
      <RecipientsEditor
        recipients={form.recipients}
        error={errorOf("recipients")}
        disabled={disabled}
        onChange={(next) => {
          set("recipients", next);
        }}
      />
    </>
  );
}

/** The SMTP server, its encryption, the account and the sender. */
function SmtpAccountFields({
  form,
  stored,
  set,
  setSecurity,
  errorOf,
  disabled,
  options,
  securityDescription,
}: {
  form: SmtpForm;
  stored: SmtpSettings;
  set: <K extends keyof SmtpForm>(name: K, value: SmtpForm[K]) => void;
  setSecurity: (next: SmtpSecurity) => void;
  errorOf: (name: SmtpField) => string | undefined;
  disabled: boolean;
  options: ReturnType<typeof smtpSecurityOptions>;
  securityDescription: string | undefined;
}) {
  const t = useT();
  return (
    <>
      <div className="grid min-w-0 gap-5 sm:grid-cols-3">
        <Field label={t("settings.notifications.email.serverLabel")} error={errorOf("host")} className="sm:col-span-2">
          <Input
            mono
            autoComplete="off"
            spellCheck={false}
            placeholder="smtp.example.com"
            value={form.host}
            disabled={disabled}
            onValueChange={(next: string) => {
              set("host", next);
            }}
          />
        </Field>
        <Field label={t("settings.notifications.email.portLabel")} error={errorOf("port")}>
          <Input
            mono
            inputMode="numeric"
            autoComplete="off"
            value={form.port}
            disabled={disabled}
            onValueChange={(next: string) => {
              set("port", next);
            }}
          />
        </Field>
      </div>
      <div className="flex min-w-0 flex-col gap-1.5">
        {/* The radio group is named by its own label; this is the same word, for sight. */}
        <span aria-hidden="true" className="text-13 font-medium text-fg">
          {t("settings.notifications.email.encryptionLabel")}
        </span>
        <SegmentedControl<SmtpSecurity>
          label={t("settings.notifications.email.encryptionLabel")}
          options={options}
          value={form.security}
          onValueChange={(next) => {
            if (!disabled) setSecurity(next);
          }}
          className="self-start"
        />
        <p className="text-12 text-fg-muted">{securityDescription}</p>
        {errorOf("security") !== undefined ? <p className="text-13 text-fail">{errorOf("security")}</p> : null}
      </div>
      <div className="grid min-w-0 gap-5 sm:grid-cols-2">
        <Field label={t("settings.notifications.email.usernameLabel")} optional error={errorOf("username")} description={t("settings.notifications.email.usernameDescription")}>
          <Input
            mono
            autoComplete="off"
            spellCheck={false}
            value={form.username}
            disabled={disabled}
            onValueChange={(next: string) => {
              set("username", next);
            }}
          />
        </Field>
        <Field label={t("settings.notifications.email.passwordLabel")} optional error={errorOf("password")} description={t("settings.notifications.email.passwordDescription")}>
          <SecretInput
            label={t("settings.notifications.email.passwordLabel")}
            placeholder=""
            value={form.password}
            configured={stored.password_set}
            disabled={disabled}
            onChange={(next) => {
              set("password", next);
            }}
          />
        </Field>
      </div>
      <Field label={t("settings.notifications.email.fromAddressLabel")} optional error={errorOf("from_address")} description={t("settings.notifications.email.fromAddressDescription")}>
        <Input
          mono
          type="email"
          autoComplete="off"
          spellCheck={false}
          placeholder="noust@example.com"
          value={form.from_address}
          disabled={disabled}
          onValueChange={(next: string) => {
            set("from_address", next);
          }}
        />
      </Field>
    </>
  );
}

/**
 * Email: on or off, and the SMTP account it goes through, as one form in its drawer. The
 * password is write-only: GET /api/config/smtp only says whether one is stored, a field left
 * empty keeps it, and the server refuses to keep it for a different server or account.
 */
export function EmailChannel({ enabledStored, smtp }: { enabledStored: boolean; smtp: SmtpSettings | undefined }) {
  const t = useT();
  const query = useQuery(smtpSettingsQuery());
  const refresh = useRefreshConfig();
  const test = useChannelTest("email");
  const [open, setOpen] = useState(false);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [draft, setDraft] = useState<Partial<SmtpForm>>({});
  // The email service, when the server can say (an older one answers an error, and there is none to offer).
  const providerQuery = useQuery(emailApiQuery());
  const [providerDraft, setProviderDraft] = useState<Partial<EmailProviderForm>>({});
  const providerStored = providerQuery.data === undefined ? undefined : providerFormFrom(providerQuery.data);
  const providerForm: EmailProviderForm | undefined = providerStored === undefined ? undefined : { ...providerStored, ...providerDraft };
  // The same for the service's fields.
  const [providerEdited, setProviderEdited] = useState<ReadonlySet<string>>(new Set());
  const providerDirty = providerForm !== undefined && providerStored !== undefined && providerFormDirty(providerForm, providerStored);
  // A field's error is hidden once it is edited: the message is about the value that was sent.
  const [edited, setEdited] = useState<ReadonlySet<SmtpField>>(new Set());

  const data = query.data ?? smtp;
  const stored = data === undefined ? undefined : smtpFormFrom(data);
  const form: SmtpForm | undefined = stored === undefined ? undefined : { ...stored, ...draft };
  const enabledValue = enabled ?? enabledStored;
  const enabledDirty = enabled !== null && enabled !== enabledStored;
  const smtpDirty = form !== undefined && stored !== undefined && smtpFormDirty(form, stored);
  const dirty = enabledDirty || smtpDirty || providerDirty;

  const save = useMutation({
    mutationFn: async ({ values, turnOn, service }: { values: SmtpForm | null; turnOn: boolean | null; service: EmailProviderForm | null }) => {
      // The service first, then the account: turning email on over what the server refused would send nothing.
      if (service !== null) await saveEmailApiSettings(providerBody(service));
      if (values !== null) await saveSmtpSettings(smtpBody(values));
      // Through the channel API: the block written whole would lose its recipients and routing.
      if (turnOn !== null) await saveNotificationChannel("email", { enabled: turnOn });
      await refresh();
    },
    onSuccess: () => {
      setDraft({});
      setProviderDraft({});
      setEnabled(null);
      test.reset();
    },
    onSettled: () => {
      setEdited(new Set());
    },
  });
  const split = splitConfigErrors(save.error, SMTP_FIELDS, SMTP_CONFIG_KEYS);
  const errorOf = (name: SmtpField): string | undefined => (edited.has(name) ? undefined : split.fields[name]);
  const providerSplit = splitConfigErrors(save.error, ["provider", "from_address", "region", "api_key", "access_key_id", "secret_access_key"] as const, PROVIDER_CONFIG_KEYS);
  const providerErrorOf = (name: EmailProviderField | "provider"): string | undefined => (providerEdited.has(name) ? undefined : providerSplit.fields[name]);
  const set = <K extends keyof SmtpForm>(name: K, value: SmtpForm[K], field: SmtpField = name): void => {
    setDraft((current) => ({ ...current, [name]: value }));
    setEdited((current) => new Set([...current, field]));
  };
  const reset = (): void => {
    setDraft({});
    setProviderDraft({});
    setEnabled(null);
    setEdited(new Set());
    setProviderEdited(new Set());
    save.reset();
  };

  // A service in place of SMTP counts as the account once it has what it needs and there is somebody to write to.
  const serviceSet = providerQuery.data?.configured === true;
  const viaService = (providerForm?.provider ?? "") !== "";
  const accountSet = serviceSet || (data !== undefined && data.host !== "" && data.recipients.length > 0);
  const on = accountSet && enabledStored;
  const testReason = !accountSet
    ? t("settings.notifications.email.testReasonNotConfigured")
    : dirty
      ? t("settings.notifications.channels.testReasonDirty")
      : !enabledStored
        ? t("settings.notifications.email.testReasonDisabled")
        : undefined;
  const label = t("settings.notifications.email.label");
  const description = t("settings.notifications.email.description");
  const detail = serviceSet
    ? t(enabledStored ? "settings.notifications.email.sendsVia" : "settings.notifications.email.serviceOnly", {
        count: providerQuery.data?.recipients.length ?? 0,
        provider: providerName(t, providerStored?.provider ?? ""),
      })
    : data === undefined || data.host === ""
      ? undefined
      : t.rich(enabledStored ? "settings.notifications.email.sendsTo" : "settings.notifications.email.accountOnly", {
          count: data.recipients.length,
          host: <Mono key="host">{data.host}</Mono>,
        });

  return (
    <>
      <ChannelRow
        name="email"
        label={label}
        description={description}
        on={on}
        configured={accountSet}
        {...(detail !== undefined ? { detail } : {})}
        test={test}
        testSource={t("settings.notifications.email.testSourceLabel")}
        testReason={enabledStored ? undefined : t("settings.notifications.email.testReasonDisabled")}
        onOpen={() => {
          setOpen(true);
        }}
      />
      <ChannelDrawer
        open={open}
        onOpenChange={setOpen}
        title={label}
        description={description}
        dirty={dirty}
        saving={save.isPending}
        onSubmit={() => {
          if (form !== undefined) save.mutate({ values: smtpDirty ? form : null, turnOn: enabledDirty ? enabledValue : null, service: providerDirty ? providerForm : null });
        }}
        onDiscard={reset}
        test={test}
        testSource={t("settings.notifications.email.testSourceLabel")}
        testReason={testReason}
      >
        <FormFailure error={split.form !== null && providerSplit.form !== null ? split.form : null} title={t("settings.notifications.email.saveErrorTitle")} />
        {query.isError && data === undefined ? (
          <ErrorBlock compact error={query.error} title={t("settings.notifications.email.loadFailed")} onRetry={() => void query.refetch()} />
        ) : form === undefined || data === undefined ? (
          <div aria-busy="true">
            <span className="sr-only">{t("settings.notifications.email.loading")}</span>
            <FieldsSkeleton rows={[2, 1, 2, 1]} />
          </div>
        ) : (
          <EmailFields
            form={form}
            stored={data}
            enabled={enabledValue}
            onEnabled={setEnabled}
            set={(name, value) => {
              set(name, value);
            }}
            setSecurity={(next) => {
              setDraft((current) => ({ ...current, security: next, port: portForSecurity(form.port, form.security, next) }));
              setEdited((current) => new Set([...current, "security", "port"]));
            }}
            errorOf={errorOf}
            disabled={save.isPending}
            viaService={viaService}
            provider={
              providerForm !== undefined && providerQuery.data !== undefined ? (
                <EmailProviderFields
                  form={providerForm}
                  stored={providerQuery.data}
                  set={(change) => {
                    setProviderDraft((current) => ({ ...current, ...change }));
                    setProviderEdited((current) => new Set([...current, ...Object.keys(change)]));
                  }}
                  errorOf={providerErrorOf}
                  disabled={save.isPending}
                />
              ) : null
            }
          />
        )}
      </ChannelDrawer>
    </>
  );
}
