import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { configQuery, patchConfig, smtpSettingsQuery } from "../../api/queries/config";
import type { SmtpSettings } from "../../api/queries/config";
import { notificationChannelsQuery } from "../../api/queries/notifications";
import { useDocumentTitle } from "../../app/documentTitle";
import { LOCALE_CHOICES } from "../../app/locale";
import type { Locale } from "../../app/locale";
import { useNow } from "../../components/page/clock";
import { useCan } from "../../components/page/permissions";
import { QueryState } from "../../components/page/QueryState";
import { SaveBar } from "../../components/page/SaveBar";
import { Section, Sections } from "../../components/page/Section";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field, FieldGroup } from "../../components/ui/Field";
import { Mono } from "../../components/ui/Mono";
import { SectionSwitch, SwitchedRegion } from "../../components/ui/SectionSwitch";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Skeleton } from "../../components/ui/Skeleton";
import { Textarea } from "../../components/ui/Textarea";
import { TextLink } from "../../components/ui/TextLink";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { useSaveBar } from "../app/settings/formParts";
import { DeliveryLog } from "./DeliveryLog";
import { FleetBlock } from "./fleet/FleetBlock";
import { FleetStrip } from "./fleet/FollowLine";
import { EmailChannel } from "./EmailChannel";
import { HttpChannel } from "./HttpChannel";
import { MoreChannels } from "./MoreChannels";
import { EVENT_GROUPS, EVENT_KINDS, channels, eventSpec, isChannelConfigured, parseHostList, publicUrlOf, readNotificationSettings } from "./notifications";
import type { EventGroupId, NotificationSettings as Settings } from "./notifications";
import { FormFailure, useRefreshConfig } from "./SettingsForm";
import { SettingsMasterSwitch } from "./SettingsPrimaryAction";
import { MuteDialog } from "./mute/MuteDialog";
import { pausedUntil, untilLabel } from "./mute/mute";
import { useResume } from "./mute/useMute";
import { TelegramChannel } from "./TelegramChannel";
import { useSettingsForm } from "./useSettingsForm";
import type { SettingsForm } from "./useSettingsForm";

// ---------------------------------------------------------------------------------------
// The master switch: the page's, in its header, and what it governs is drawn off when it is (item 106).

/**
 * Whether anything is sent. It is not a channel, so it is not a row among them: it is in the header of the
 * page, beside its title, and the channels and events under it are dimmed while it is
 * off (`SwitchedRegion`). It applies at once and is not part of the form below. "On" with nowhere to go is
 * not on, which the line over the channels says with the one sentence that fixes it.
 */
function DeliverySwitch({ settings, paused }: { settings: Settings; paused: boolean }) {
  const t = useT();
  const allowed = useCan("settings.manage");
  const [asking, setAsking] = useState(false);
  const resume = useResume();
  const state = { enabled: settings.enabled, mutedUntil: settings.mutedUntil };
  // While the write is in flight (and "Confirm it's you" is open) the switch shows where it is going.
  const checked = resume.isPending ? true : settings.enabled && !paused;
  const change = (next: boolean): void => {
    // Turning them off asks until when ("for a while" is nearly always the answer); turning them on asks nothing.
    if (!next) {
      setAsking(true);
      return;
    }
    resume.mutate(state, {
      onError: (error) => {
        reportActionError(t("settings.notifications.delivery.turnOnFailed"), error);
      },
    });
  };
  const label = t("settings.notifications.delivery.switchLabel");
  return (
    <SettingsMasterSwitch>
      {allowed ? (
        <SectionSwitch label={label} checked={checked} busy={resume.isPending} onCheckedChange={change} />
      ) : (
        <SectionSwitch label={label} checked={checked} onCheckedChange={change} disabled reason={t("settings.notifications.delivery.noPermission")} />
      )}
      {asking ? (
        <MuteDialog
          state={state}
          onClose={() => {
            setAsking(false);
          }}
        />
      ) : null}
    </SettingsMasterSwitch>
  );
}

/** What the pause says while there is one: until when, and the way to end it. The page under it is dimmed, as when the switch is off. */
function PausedLine({ until, now, settings }: { until: Date; now: number; settings: Settings }) {
  const t = useT();
  const allowed = useCan("settings.manage");
  const resume = useResume();
  return (
    <InlineMessage tone="warning">
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span>{t("settings.notifications.mute.silencedUntil", { when: untilLabel(until, new Date(now)) })}</span>
        {allowed ? (
          <Button
            size="sm"
            loading={resume.isPending}
            onClick={() => {
              resume.mutate(
                { enabled: settings.enabled, mutedUntil: settings.mutedUntil },
                {
                  onError: (error) => {
                    reportActionError(t("settings.notifications.delivery.turnOnFailed"), error);
                  },
                },
              );
            }}
          >
            {t("settings.notifications.mute.turnOn")}
          </Button>
        ) : null}
      </span>
    </InlineMessage>
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
  const on = channelsOn(t, settings, smtp);
  return (
    <Section icon={OBJECT_ICONS.channel} title={t("settings.notifications.channels.title")}>
      {!settings.enabled ? (
        <InlineMessage tone="warning">{t("settings.notifications.delivery.helpOff")}</InlineMessage>
      ) : on === 0 ? (
        <InlineMessage tone="warning">{t("settings.notifications.delivery.helpNoChannel")}</InlineMessage>
      ) : null}
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
    <Section icon={OBJECT_ICONS.activity} title={t("settings.notifications.events.title")} description={t("settings.notifications.events.description")}>
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

/**
 * The languages the server writes its messages in. Its catalogs are English and Spanish and
 * `notifications.language` refuses any other, so the console's released community languages (which
 * are the console's own) are not offered here: choosing one could not be saved.
 */
const MESSAGE_LANGUAGES: readonly Locale[] = ["en", "es"];

/** The language Noust writes its messages in, and where their link leads. */
function Messages({ language, publicUrl }: { language: SettingsForm<{ language: string }>; publicUrl: string }) {
  const t = useT();
  const value: Locale = language.values?.language === "es" ? "es" : "en";
  return (
    <div className="grid min-w-0 gap-5 sm:grid-cols-2">
      <FieldGroup legend={t("settings.notifications.language.fieldLabel")} description={t("settings.notifications.language.description")} error={language.fieldErrors.language}>
        <SegmentedControl<Locale>
          label={t("settings.notifications.language.fieldLabel")}
          options={LOCALE_CHOICES.filter((choice) => MESSAGE_LANGUAGES.includes(choice.value))}
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
                  <TextLink key="general" to="/server/machine">
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
  // A pause ends by itself, so the page is read against the clock: the minute it ends, it is on again.
  const now = useNow(() => 30_000);
  const until = pausedUntil(settings.mutedUntil, new Date(now));
  return (
    <>
      <FleetStrip />
      <DeliverySwitch settings={settings} paused={until !== null} />
      {until !== null ? <PausedLine until={until} now={now} settings={settings} /> : null}
      <SwitchedRegion on={settings.enabled && until === null} className="flex flex-col gap-8">
        <Channels settings={settings} smtp={smtp} />
        <MoreChannels />
        <EventsCard form={events} language={language} privateHosts={privateHosts} publicUrl={publicUrl} />
      </SwitchedRegion>
      {/* What the central carries to every server: the servers' own channels are theirs, and this switch is the central's. */}
      <FleetBlock resource="channel" />
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
      <Section title={t("settings.notifications.channels.title")} icon={OBJECT_ICONS.channel} description={<Skeleton className="h-4 w-80 max-w-full" />}>
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
