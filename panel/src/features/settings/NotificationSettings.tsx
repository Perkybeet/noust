import { useMutation, useQuery } from "@tanstack/react-query";

import { configQuery, patchConfig, smtpSettingsQuery } from "../../api/queries/config";
import type { SmtpSettings } from "../../api/queries/config";
import { notificationChannelsQuery } from "../../api/queries/notifications";
import { useDocumentTitle } from "../../app/documentTitle";
import { LOCALE_CHOICES } from "../../app/locale";
import type { Locale } from "../../app/locale";
import { CommandHint } from "../../components/page/CommandHint";
import { QueryState } from "../../components/page/QueryState";
import { SaveBar } from "../../components/page/SaveBar";
import { Section, Sections } from "../../components/page/Section";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field, FieldGroup } from "../../components/ui/Field";
import { Mono } from "../../components/ui/Mono";
import { Setting } from "../../components/ui/Setting";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { Textarea } from "../../components/ui/Textarea";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { useSaveBar } from "../app/settings/formParts";
import { DeliveryLog } from "./DeliveryLog";
import { EmailChannel } from "./EmailChannel";
import { HttpChannel } from "./HttpChannel";
import { MoreChannels } from "./MoreChannels";
import { EVENT_GROUPS, EVENT_KINDS, channels, eventSpec, isChannelConfigured, parseHostList, publicUrlOf, readNotificationSettings } from "./notifications";
import type { EventGroupId, NotificationSettings as Settings } from "./notifications";
import { FormFailure, useRefreshConfig } from "./SettingsForm";
import { TelegramChannel } from "./TelegramChannel";
import { useSettingsForm } from "./useSettingsForm";
import type { SettingsForm } from "./useSettingsForm";

// ---------------------------------------------------------------------------------------
// The master switch: applies at once, so it is not part of the form below.

/**
 * Whether anything is sent, as the first row of the channels' card. The state is the switch and a
 * small pill; "on" with nowhere to go is not on, so it says so in amber, with the one sentence
 * that fixes it, and no block.
 */
function DeliveryRow({ settings, channelsOn }: { settings: Settings; channelsOn: number }) {
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
  const help = !checked
    ? t("settings.notifications.delivery.helpOff")
    : channelsOn === 0
      ? t("settings.notifications.delivery.helpNoChannel")
      : t("settings.notifications.delivery.helpOn", { count: channelsOn });
  const status = !checked ? (
    <StatusPill state="stopped" label={t("settings.notifications.delivery.off")} size="sm" />
  ) : channelsOn === 0 ? (
    <StatusPill state="warning" label={t("settings.notifications.delivery.onNoChannel")} size="sm" />
  ) : (
    <StatusPill state="running" label={t("settings.notifications.delivery.on")} size="sm" />
  );
  return (
    <Setting
      label={t("settings.notifications.delivery.switchLabel")}
      help={help}
      status={status}
      checked={checked}
      busy={toggle.isPending}
      onCheckedChange={(next) => {
        toggle.mutate(next);
      }}
    />
  );
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
  return (
    <Section title={t("settings.notifications.channels.title")}>
      <Card padding="none">
        <div className="flex min-w-0 flex-col divide-y divide-border">
          <DeliveryRow settings={settings} channelsOn={channelsOn(t, settings, smtp)} />
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
        </div>
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
      <div className="flex min-w-0 flex-col gap-3">
        {EVENT_GROUPS.map((group) => (
          <EventGroup
            key={group.id}
            id={group.id}
            kinds={group.kinds}
            values={values}
            changed={group.kinds.some((kind) => form.changed.includes(kind))}
            onChange={(kind, next) => form.set(kind, next)}
          />
        ))}
        <Card>
          <Messages language={language} publicUrl={publicUrl} />
        </Card>
        <PrivateHosts form={privateHosts} />
      </div>
    </Section>
  );
}

/**
 * One area's events, folded to a line that says how many are sent: the page stays short, and
 * what each event is opens on demand. A group with an unsaved change starts open.
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
    <Disclosure
      variant="card"
      label={title}
      summary={sent === 0 ? t("settings.notifications.events.noneSent") : t("settings.notifications.events.sent", { sent, total: kinds.length })}
      defaultOpen={changed}
    >
      <div className="flex min-w-0 flex-col gap-3">
        <p className="text-12 text-pretty text-fg-muted">{t(`settings.notifications.events.groups.${id}.description`)}</p>
        <fieldset className="m-0 grid min-w-0 gap-x-8 gap-y-3 border-0 p-0 sm:grid-cols-2">
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
      </div>
    </Disclosure>
  );
}

/** The language Noust writes its messages in, and where their link leads. */
function Messages({ language, publicUrl }: { language: SettingsForm<{ language: string }>; publicUrl: string }) {
  const t = useT();
  const value: Locale = language.values?.language === "es" ? "es" : "en";
  return (
    <div className="grid min-w-0 gap-5 sm:grid-cols-2">
      <FieldGroup legend={t("settings.notifications.language.fieldLabel")} description={t("settings.notifications.language.description")} error={language.fieldErrors.language}>
        <SegmentedControl<Locale>
          label={t("settings.notifications.language.fieldLabel")}
          options={LOCALE_CHOICES.map((choice) => ({ value: choice.value, label: choice.label }))}
          value={value}
          onValueChange={(next) => {
            language.set("language", next);
          }}
          className="self-start"
        />
      </FieldGroup>
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
    <Disclosure variant="card" label={t("settings.notifications.privateHosts.title")} defaultOpen={open}>
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
    </Disclosure>
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
      <MoreChannels />
      <EventsCard form={events} language={language} privateHosts={privateHosts} publicUrl={publicUrl} />
      <CommandHint command="noust notify test telegram" label={t("settings.notifications.fromTerminal")} />
      <DeliveryLog />
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
  // The channels' own routing is drawn with their rows, so the page waits for it as well (a server
  // that predates it answers with an error, which only means there is no routing to draw).
  const channelList = useQuery(notificationChannelsQuery());
  const ready = {
    data: channelList.isPending ? undefined : query.data,
    error: query.error,
    isPending: query.isPending || channelList.isPending,
    isError: query.isError,
    isRefetching: query.isRefetching,
    refetch: query.refetch,
  };
  return (
    <Sections>
      <QueryState query={ready} label={t("settings.notifications.loadingLabel")} skeleton={<NotificationsSkeleton />}>
        {(data) => <NotificationsForm settings={readNotificationSettings(data.config)} publicUrl={publicUrlOf(data.config)} smtp={smtp.data} />}
      </QueryState>
    </Sections>
  );
}
