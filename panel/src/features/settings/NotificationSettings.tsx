import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronDown, KeyRound } from "lucide-react";
import { useState } from "react";

import { configQuery, patchConfig, smtpSettingsQuery } from "../../api/queries/config";
import type { SmtpSettings } from "../../api/queries/config";
import { useDocumentTitle } from "../../app/documentTitle";
import { LOCALE_CHOICES } from "../../app/locale";
import type { Locale } from "../../app/locale";
import { CommandHint } from "../../components/page/CommandHint";
import { QueryState } from "../../components/page/QueryState";
import { SaveBar } from "../../components/page/SaveBar";
import { Section, Sections } from "../../components/page/Section";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { CopyButton } from "../../components/ui/CopyButton";
import { Field } from "../../components/ui/Field";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { FeatureState } from "../../components/ui/FeatureState";
import { Switch } from "../../components/ui/Switch";
import { Textarea } from "../../components/ui/Textarea";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { useSaveBar } from "../app/settings/formParts";
import { ChannelDrawer, ChannelRow, SecretInput, testReasonFor, useChannelTest } from "./channelParts";
import { EmailChannel } from "./EmailChannel";
import { EVENT_GROUPS, EVENT_KINDS, REDACTED, channelValue, channels, eventSpec, isChannelConfigured, parseHostList, publicUrlOf, readNotificationSettings } from "./notifications";
import type { ChannelSpec, EventGroupId, NotificationSettings as Settings } from "./notifications";
import { FormFailure, useRefreshConfig } from "./SettingsForm";
import { TelegramChannel } from "./TelegramChannel";
import { useSettingsForm } from "./useSettingsForm";
import type { SettingsForm } from "./useSettingsForm";

/** 32 random bytes in hex: a signing secret nobody typed. */
function newSecret(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

// ---------------------------------------------------------------------------------------
// The master switch: applies at once, so it is not part of the form below.

function useDelivery(settings: Settings, channelsOn: number) {
  const t = useT();
  const refresh = useRefreshConfig();
  const toggle = useMutation({
    mutationFn: async (next: boolean) => {
      await patchConfig("notifications.enabled", next);
      await refresh();
    },
    onError: (error, next) => {
      reportActionError(next ? t("settings.notifications.delivery.turnOnFailed") : t("settings.notifications.delivery.turnOffFailed"), error);
    },
  });
  // While the write is in flight (and "Confirm it's you" is open) the switch shows where it is going.
  const checked = toggle.isPending ? toggle.variables : settings.enabled;
  const description = !checked
    ? channelsOn === 0
      ? t("settings.notifications.delivery.offNoChannel")
      : t("settings.notifications.delivery.off")
    : channelsOn === 0
      ? t("settings.notifications.delivery.onNoChannel")
      : t("settings.notifications.delivery.on", { count: channelsOn });
  // On but with nowhere to go is not on: amber, not green (DESIGN, FeatureState).
  const state = !checked ? "off" : channelsOn === 0 ? "problem" : "on";
  const title =
    state === "off"
      ? t("settings.notifications.delivery.offTitle")
      : state === "problem"
        ? t("settings.notifications.delivery.problemTitle")
        : t("settings.notifications.delivery.onTitle");
  return {
    state: (
      <FeatureState
        state={state}
        title={title}
        action={
          <Switch
            label={t("settings.notifications.delivery.switchLabel")}
            checked={checked}
            disabled={toggle.isPending}
            onCheckedChange={(next) => {
              toggle.mutate(next);
            }}
          />
        }
      >
        {description}
      </FeatureState>
    ),
  };
}

// ---------------------------------------------------------------------------------------
// Webhook, Slack and Discord: a URL (and, for the webhook, a signing secret).

function HttpChannel({ spec, stored }: { spec: ChannelSpec; stored: Readonly<Record<string, string>> }) {
  const t = useT();
  const refresh = useRefreshConfig();
  const test = useChannelTest(spec.id);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [cleared, setCleared] = useState<ReadonlySet<string>>(new Set());
  const [generated, setGenerated] = useState<string | null>(null);

  const configured = isChannelConfigured(spec, stored);
  const dirty = cleared.size > 0 || spec.fields.some((field) => (draft[field.key] ?? "").trim() !== "");
  const save = useMutation({
    mutationFn: async (forget: ReadonlySet<string>) => {
      await patchConfig(`notifications.channels.${spec.id}`, channelValue(spec, stored, draft, forget));
      await refresh();
    },
    onSuccess: () => {
      reset();
      test.reset();
    },
  });
  const reset = (): void => {
    setDraft({});
    setCleared(new Set());
    setGenerated(null);
    save.reset();
  };

  const signed = spec.id === "webhook" && stored["secret"] === REDACTED;
  const detail =
    spec.id === "webhook" && configured
      ? signed
        ? t("settings.notifications.channels.webhook.signed")
        : t("settings.notifications.channels.webhook.unsigned")
      : undefined;
  const destinationKeys = new Set(spec.fields.filter((field) => field.secret).map((field) => field.key));

  return (
    <>
      <ChannelRow
        label={spec.label}
        description={spec.description}
        on={configured}
        configured={configured}
        {...(detail !== undefined ? { detail } : {})}
        test={test}
        testReason={undefined}
        onOpen={() => {
          setOpen(true);
        }}
      />
      <ChannelDrawer
        open={open}
        onOpenChange={setOpen}
        title={spec.label}
        description={spec.description}
        dirty={dirty}
        saving={save.isPending}
        onSubmit={() => {
          save.mutate(cleared);
        }}
        onDiscard={reset}
        test={test}
        testReason={testReasonFor(t, configured, dirty)}
        remove={
          configured
            ? {
                label: t("settings.notifications.channels.remove"),
                title: t("settings.notifications.channels.removeTitle", { label: spec.label }),
                description: t("settings.notifications.channels.removeDescription", { label: spec.label }),
                run: async () => {
                  await patchConfig(`notifications.channels.${spec.id}`, channelValue(spec, stored, {}, destinationKeys));
                  await refresh();
                  test.reset();
                },
              }
            : undefined
        }
      >
        <FormFailure error={save.error !== null && !hasFieldError(save.error, spec) ? save.error : null} title={t("settings.notifications.channels.saveErrorTitle", { label: spec.label })} />
        {spec.fields.map((field) => {
          const isStored = stored[field.key] === REDACTED;
          const forgetting = cleared.has(field.key);
          const generate =
            spec.id === "webhook" && field.key === "secret" ? (
              <Button
                icon={<KeyRound aria-hidden="true" />}
                disabled={save.isPending}
                onClick={() => {
                  const secret = newSecret();
                  setGenerated(secret);
                  setDraft((current) => ({ ...current, secret }));
                  setCleared((current) => new Set([...current].filter((key) => key !== "secret")));
                }}
              >
                {t("settings.notifications.channels.webhook.generate")}
              </Button>
            ) : undefined;
          return (
            <div key={field.key} className="flex min-w-0 flex-col gap-2">
              <Field
                label={field.label}
                {...(field.optional === true ? { optional: true } : {})}
                description={field.description}
                error={fieldError(save.error, field.key)}
                {...(generate !== undefined ? { action: generate } : {})}
              >
                <SecretInput
                  label={field.label}
                  placeholder={field.placeholder}
                  value={draft[field.key] ?? ""}
                  configured={isStored && !forgetting}
                  disabled={save.isPending || forgetting}
                  onChange={(next) => {
                    setDraft((current) => ({ ...current, [field.key]: next }));
                    if (generated !== null && field.key === "secret") setGenerated(null);
                  }}
                />
              </Field>
              {generated !== null && field.key === "secret" && draft["secret"] === generated ? (
                <div className="flex min-w-0 items-center gap-2 rounded-control border border-border bg-bg-sunken py-1 pr-1 pl-3">
                  <Mono truncate className="min-w-0 flex-1 text-12">
                    {generated}
                  </Mono>
                  <CopyButton value={generated} label={t("settings.notifications.channels.webhook.secretCopyLabel")} />
                </div>
              ) : null}
              {generated !== null && field.key === "secret" && draft["secret"] === generated ? (
                <p className="text-12 text-fg-muted">{t("settings.notifications.channels.webhook.generatedHint")}</p>
              ) : null}
              {field.optional === true && isStored ? (
                <Checkbox
                  label={t("settings.notifications.channels.webhook.stopSigning")}
                  checked={forgetting}
                  disabled={save.isPending}
                  onCheckedChange={(next) => {
                    setCleared((current) => (next ? new Set([...current, field.key]) : new Set([...current].filter((key) => key !== field.key))));
                    if (next) setDraft((current) => ({ ...current, [field.key]: "" }));
                  }}
                />
              ) : null}
            </div>
          );
        })}
      </ChannelDrawer>
    </>
  );
}

function fieldError(error: unknown, key: string): string | undefined {
  if (error === null || error === undefined || typeof error !== "object") return undefined;
  const fields = (error as { fields?: Record<string, string> | null }).fields;
  return fields?.[key];
}

function hasFieldError(error: unknown, spec: ChannelSpec): boolean {
  return spec.fields.some((field) => fieldError(error, field.key) !== undefined);
}

// ---------------------------------------------------------------------------------------
// The channels, as a list

/**
 * The channels, under the one switch for all of them: whether anything is sent is the first
 * thing the page says, beside the channels it would go to.
 */
function Channels({ settings, smtp }: { settings: Settings; smtp: SmtpSettings | undefined }) {
  const t = useT();
  const specs = channels(t);
  const delivery = useDelivery(settings, channelsOn(t, settings, smtp));
  return (
    <Section title={t("settings.notifications.channels.title")}>
      {delivery.state}
      <Card padding="none">
        <ul aria-label={t("settings.notifications.channels.listLabel")} className="flex min-w-0 flex-col divide-y divide-border">
          {specs.map((spec) =>
            spec.id === "email" ? (
              <EmailChannel key={spec.id} enabledStored={settings.emailEnabled} smtp={smtp} />
            ) : spec.id === "telegram" ? (
              <TelegramChannel key={spec.id} stored={settings.channels.telegram} />
            ) : (
              <HttpChannel key={spec.id} spec={spec} stored={settings.channels[spec.id]} />
            ),
          )}
        </ul>
      </Card>
    </Section>
  );
}

/** How many channels send something: a destination (and, for email, turned on with a recipient). */
function channelsOn(t: T, settings: Settings, smtp: SmtpSettings | undefined): number {
  let count = 0;
  for (const spec of channels(t)) {
    if (spec.id === "email") {
      if (settings.emailEnabled && smtp !== undefined && smtp.host !== "" && smtp.recipients.length > 0) count += 1;
    } else if (isChannelConfigured(spec, settings.channels[spec.id])) {
      count += 1;
    }
  }
  return count;
}

// ---------------------------------------------------------------------------------------
// The form: events, language and private destinations, saved from one bar.

function EventsCard({
  form,
  language,
  privateHosts,
  publicUrl,
}: {
  form: SettingsForm<Record<string, boolean>>;
  language: SettingsForm<{ language: string }>;
  privateHosts: SettingsForm<{ allow_private_hosts: string }>;
  publicUrl: string;
}) {
  const t = useT();
  const values = form.values ?? {};
  return (
    <Section title={t("settings.notifications.events.title")} description={t("settings.notifications.events.description")}>
      <FormFailure error={form.formError} title={t("settings.notifications.events.errorTitle")} />
      <FormFailure error={language.formError ?? privateHosts.formError} title={t("settings.notifications.messages.errorTitle")} />
      <Card padding="none">
        <ul className="flex min-w-0 flex-col divide-y divide-border">
          {EVENT_GROUPS.map((group) => (
            <li key={group.id} className="min-w-0">
              <EventGroup
                id={group.id}
                kinds={group.kinds}
                values={values}
                changed={group.kinds.some((kind) => form.changed.includes(kind))}
                onChange={(kind, next) => form.set(kind, next)}
              />
            </li>
          ))}
          <li className="min-w-0 px-4 py-4 sm:px-5">
            <Messages language={language} publicUrl={publicUrl} />
          </li>
          <li className="min-w-0">
            <PrivateHosts form={privateHosts} />
          </li>
        </ul>
      </Card>
    </Section>
  );
}

/**
 * One area's events, folded to a line that says how many are sent: the page stays short, and
 * what each event is opens on demand. A group with an unsaved change stays open.
 */
function EventGroup({
  id,
  kinds,
  values,
  changed,
  onChange,
}: {
  id: EventGroupId;
  kinds: readonly string[];
  values: Readonly<Record<string, boolean>>;
  changed: boolean;
  onChange: (kind: string, next: boolean) => void;
}) {
  const t = useT();
  const title = t(`settings.notifications.events.groups.${id}.title`);
  const sent = kinds.filter((kind) => values[kind] === true).length;
  return (
    <details open={changed || undefined} className="group">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 -outline-offset-2 hover:bg-surface-hover sm:px-5 [&::-webkit-details-marker]:hidden">
        <span className="flex min-w-0 flex-col">
          <span className="text-13 font-medium text-fg">{title}</span>
          <span className="text-12 text-fg-muted">{t(`settings.notifications.events.groups.${id}.description`)}</span>
        </span>
        <span className="flex shrink-0 items-center gap-2 text-12 text-fg-muted tabular-nums">
          {sent === 0 ? t("settings.notifications.events.noneSent") : t("settings.notifications.events.sent", { sent, total: kinds.length })}
          <ChevronDown aria-hidden="true" className="size-icon-sm transition-transform duration-(--duration-fast) group-open:rotate-180" />
        </span>
      </summary>
      <fieldset className="grid min-w-0 gap-x-8 gap-y-3 px-4 pt-1 pb-4 sm:grid-cols-2 sm:px-5">
        <legend className="sr-only">{title}</legend>
        {kinds.map((kind) => {
          const spec = eventSpec(t, kind);
          return (
            <Checkbox
              key={kind}
              label={
                <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  {spec.label}
                  {spec.offByDefault === true ? <Badge>{t("settings.notifications.events.offByDefault")}</Badge> : null}
                </span>
              }
              description={spec.description}
              checked={values[kind] === true}
              onCheckedChange={(next) => {
                onChange(kind, next);
              }}
            />
          );
        })}
      </fieldset>
    </details>
  );
}

/** The language Noust writes its messages in, and where their link leads. */
function Messages({ language, publicUrl }: { language: SettingsForm<{ language: string }>; publicUrl: string }) {
  const t = useT();
  const value: Locale = language.values?.language === "es" ? "es" : "en";
  return (
    <div className="grid min-w-0 gap-5 sm:grid-cols-2">
      <div className="flex min-w-0 flex-col gap-1.5">
        <span aria-hidden="true" className="text-13 font-medium text-fg">
          {t("settings.notifications.language.fieldLabel")}
        </span>
        <SegmentedControl<Locale>
          label={t("settings.notifications.language.fieldLabel")}
          options={LOCALE_CHOICES.map((choice) => ({ value: choice.value, label: choice.label }))}
          value={value}
          onValueChange={(next) => {
            language.set("language", next);
          }}
          className="self-start"
        />
        <p className="text-12 text-pretty text-fg-muted">{t("settings.notifications.language.description")}</p>
        {language.fieldErrors.language !== undefined ? <p className="text-13 text-fail">{language.fieldErrors.language}</p> : null}
      </div>
      <div className="flex min-w-0 flex-col gap-1.5">
        <p className="text-13 font-medium text-fg">{t("settings.notifications.messages.linkTitle")}</p>
        <p className="text-13 text-pretty text-fg-muted">
          {publicUrl === ""
            ? t.rich("settings.notifications.messages.noLink", {
                general: (
                  <TextLink key="general" to="/settings">
                    {t("settings.notifications.messages.generalLink")}
                  </TextLink>
                ),
              })
            : t.rich("settings.notifications.messages.link", { url: <Mono key="url">{publicUrl}</Mono> })}
        </p>
      </div>
    </div>
  );
}

/** Hosts a webhook may reach although they are private: rarely needed, so folded. */
function PrivateHosts({ form }: { form: SettingsForm<{ allow_private_hosts: string }> }) {
  const t = useT();
  const open = form.dirty || form.fieldErrors.allow_private_hosts !== undefined || (form.values?.allow_private_hosts ?? "") !== "";
  return (
    <details open={open || undefined} className="group">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 -outline-offset-2 hover:bg-surface-hover sm:px-5 [&::-webkit-details-marker]:hidden">
        <span className="text-13 font-medium text-fg">{t("settings.notifications.privateHosts.title")}</span>
        <ChevronDown aria-hidden="true" className="size-icon-sm text-fg-muted transition-transform duration-(--duration-fast) group-open:rotate-180" />
      </summary>
      <div className="px-4 pt-1 pb-4 sm:px-5">
        <Field
          label={t("settings.notifications.privateHosts.fieldLabel")}
          optional
          description={t("settings.notifications.privateHosts.fieldDescription")}
          error={form.fieldErrors.allow_private_hosts}
        >
          <Textarea
            mono
            rows={3}
            placeholder="10.0.0.12"
            value={form.values?.allow_private_hosts ?? ""}
            onChange={(event) => {
              form.set("allow_private_hosts", event.target.value);
            }}
          />
        </Field>
      </div>
    </details>
  );
}

function NotificationsForm({ settings, publicUrl, smtp }: { settings: Settings; publicUrl: string; smtp: SmtpSettings | undefined }) {
  const t = useT();
  const refresh = useRefreshConfig();
  const events = useSettingsForm<Record<string, boolean>>({
    server: settings.events,
    names: EVENT_KINDS,
    save: async (values) => {
      await patchConfig("notifications.events", Object.fromEntries(EVENT_KINDS.map((kind) => [kind, values[kind] === true])));
      await refresh();
    },
  });
  const language = useSettingsForm<{ language: string }>({
    server: { language: settings.language },
    names: ["language"],
    soleField: "language",
    save: async ({ language: value }) => {
      await patchConfig("notifications.language", value);
      await refresh();
    },
  });
  const privateHosts = useSettingsForm<{ allow_private_hosts: string }>({
    server: { allow_private_hosts: settings.allowPrivateHosts.join("\n") },
    names: ["allow_private_hosts"],
    soleField: "allow_private_hosts",
    save: async ({ allow_private_hosts }) => {
      await patchConfig("notifications.allow_private_hosts", parseHostList(allow_private_hosts));
      await refresh();
    },
  });
  const bar = useSaveBar([events.part, language.part, privateHosts.part], t("settings.notifications.notSaved"));
  return (
    <>
      <Channels settings={settings} smtp={smtp} />
      <EventsCard form={events} language={language} privateHosts={privateHosts} publicUrl={publicUrl} />
      <CommandHint command="noust notify test telegram" label={t("settings.notifications.fromTerminal")} />
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
    </>
  );
}

/** The page's shape while the configuration loads: the switch, the channel rows, the events. */
function NotificationsSkeleton() {
  const t = useT();
  return (
    <div className="flex flex-col gap-8">
      <Section title={t("settings.notifications.channels.title")} description={<Skeleton className="h-4 w-80 max-w-full" />}>
        <Card padding="none">
          <ul aria-hidden="true" className="flex flex-col divide-y divide-border">
            {channels(t).map((spec) => (
              <li key={spec.id} className="flex items-center justify-between gap-4 px-4 py-2.5 sm:px-5">
                <div className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-13 font-medium text-fg">{spec.label}</span>
                  <span className="text-12 text-fg-muted">{spec.description}</span>
                </div>
                <Skeleton className="h-control-sm w-20" />
              </li>
            ))}
          </ul>
        </Card>
      </Section>
      <Section title={t("settings.notifications.events.title")} description={t("settings.notifications.events.description")}>
        <Card padding="none">
          <ul aria-hidden="true" className="flex flex-col divide-y divide-border">
            {EVENT_GROUPS.map((group) => (
              <li key={group.id} className="flex items-center justify-between gap-4 px-4 py-3 sm:px-5">
                <div className="flex min-w-0 flex-col">
                  <span className="text-13 font-medium text-fg">{t(`settings.notifications.events.groups.${group.id}.title`)}</span>
                  <span className="text-12 text-fg-muted">{t(`settings.notifications.events.groups.${group.id}.description`)}</span>
                </div>
                <Skeleton className="h-3 w-16" />
              </li>
            ))}
          </ul>
        </Card>
      </Section>
    </div>
  );
}

/**
 * Settings > Notifications: whether anything is sent, the channels it goes to (each set up in
 * its own drawer, with a test that shows what the channel answered), which events are sent and
 * in which language.
 */
export function NotificationSettings() {
  const t = useT();
  useDocumentTitle(t("settings.notifications.documentTitle"), 1);
  const query = useQuery(configQuery());
  const smtp = useQuery(smtpSettingsQuery());
  return (
    <Sections>
      <QueryState query={query} label={t("settings.notifications.loadingLabel")} skeleton={<NotificationsSkeleton />}>
        {(data) => <NotificationsForm settings={readNotificationSettings(data.config)} publicUrl={publicUrlOf(data.config)} smtp={smtp.data} />}
      </QueryState>
    </Sections>
  );
}
