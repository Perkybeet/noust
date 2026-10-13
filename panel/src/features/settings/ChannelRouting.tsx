/**
 * What each channel hears. A channel's row says what it is limited to, and a drawer sets it:
 * which events (its own switches or the server's), which applications, projects, environments
 * and tags, the lowest severity, and the quiet hours. Saved through
 * `PUT /api/notifications/channels/{name}`, which validates a route the same way the command
 * line does and names what it refuses.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";
import type { SyntheticEvent } from "react";

import { configKeys, configQuery } from "../../api/queries/config";
import { notificationChannelsQuery, notificationKeys, saveNotificationChannel } from "../../api/queries/notifications";
import type { NotificationChannel } from "../../api/queries/notifications";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Subsection } from "../../components/page/Subsection";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Drawer } from "../../components/ui/Drawer";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Switch } from "../../components/ui/Switch";
import { useT } from "../../i18n";
import { EVENT_GROUPS, eventSpec, readNotificationSettings } from "./notifications";
import type { EventGroupId } from "./notifications";
import { QUIET_ALLOWS, isClock, routeSummary, routingBody, routingDirty, routingFormFrom } from "./routing";
import type { QuietAllow, QuietForm, RouteForm, RouteList, RoutingForm } from "./routing";
import { FormFailure } from "./SettingsForm";

/** A channel as the server describes it, read once for every row of the page. */
export function useChannelView(name: string): NotificationChannel | undefined {
  const channels = useQuery(notificationChannelsQuery());
  return channels.data?.items.find((channel) => channel.name === name);
}

/** What a channel is limited to, as short chips under its description; nothing when it hears everything. */
export function RoutingSummary({ name }: { name: string }) {
  const t = useT();
  const channel = useChannelView(name);
  if (channel === undefined) return null;
  const phrases = routeSummary(t, channel);
  if (phrases.length === 0) return null;
  return (
    <ul aria-label={t("notifyRouting.title", { label: name })} className="mt-1 flex min-w-0 flex-wrap gap-1.5">
      {phrases.map((phrase) => (
        <li key={phrase}>
          <Badge>{phrase}</Badge>
        </li>
      ))}
    </ul>
  );
}

/** The row's "Routing" button, with its drawer. Absent where the server does not manage channels by name. */
export function RoutingButton({ name, label }: { name: string; label: string }) {
  const t = useT();
  const channel = useChannelView(name);
  const [open, setOpen] = useState(false);
  if (channel === undefined) return null;
  return (
    <>
      <Button
        size="sm"
        aria-label={t("notifyRouting.buttonLabel", { label })}
        onClick={() => {
          setOpen(true);
        }}
      >
        {t("notifyRouting.button")}
      </Button>
      <RoutingDrawer channel={channel} label={label} open={open} onOpenChange={setOpen} />
    </>
  );
}

function RoutingDrawer({ channel, label, open, onOpenChange }: { channel: NotificationChannel; label: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const config = useQuery(configQuery());
  const formId = useId();
  const serverEvents = useMemo(() => (config.data === undefined ? {} : readNotificationSettings(config.data.config).events), [config.data]);
  const initial = useMemo(() => routingFormFrom(channel, serverEvents), [channel, serverEvents]);
  const [draft, setDraft] = useState<RoutingForm | null>(null);
  const form = draft ?? initial;
  const dirty = draft !== null && routingDirty(draft, initial, channel);
  const timesOk = !form.quiet.on || (isClock(form.quiet.start) && isClock(form.quiet.end));

  const save = useMutation({
    mutationFn: () => saveNotificationChannel(channel.name, routingBody(form, channel)),
    onSuccess: async () => {
      setDraft(null);
      // The channel's own switches are part of the configuration too, so the page's events read it again.
      await Promise.all([queryClient.invalidateQueries({ queryKey: notificationKeys.channels }), queryClient.invalidateQueries({ queryKey: configKeys.all })]);
    },
  });
  const discard = (): void => {
    setDraft(null);
    save.reset();
  };
  const edit = (change: (current: RoutingForm) => RoutingForm): void => {
    setDraft(change(form));
  };
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (dirty && timesOk && !save.isPending) save.mutate();
  };

  return (
    <Drawer
      open={open}
      onOpenChange={(next) => {
        if (!next) discard();
        onOpenChange(next);
      }}
      title={t("notifyRouting.title", { label })}
      description={t("notifyRouting.description")}
      footer={
        <>
          <Button
            variant="ghost"
            disabled={save.isPending}
            onClick={() => {
              discard();
              onOpenChange(false);
            }}
          >
            {t("settings.shared.done")}
          </Button>
          <Button type="submit" form={formId} variant={dirty ? "primary" : "secondary"} disabled={!dirty || !timesOk} loading={save.isPending}>
            {t("settings.shared.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex min-w-0 flex-col gap-8">
        <FormFailure error={save.error} title={t("notifyRouting.saveErrorTitle", { label })} />
        <EventsPart form={form} edit={edit} />
        <RoutePart route={form.route} onChange={(route) => edit((current) => ({ ...current, route }))} />
        <QuietPart quiet={form.quiet} onChange={(quiet) => edit((current) => ({ ...current, quiet }))} />
        {channel.type === "telegram" ? (
          <Subsection title={t("notifyRouting.topic.label")} description={t("notifyRouting.topic.description")}>
            <Field label={t("notifyRouting.topic.label")} optional>
              <Input
                mono
                inputMode="numeric"
                placeholder="12"
                value={form.topic}
                onValueChange={(next: string) => {
                  edit((current) => ({ ...current, topic: next }));
                }}
              />
            </Field>
          </Subsection>
        ) : null}
      </form>
    </Drawer>
  );
}

// ---------------------------------------------------------------------------------------
// Events

function EventsPart({ form, edit }: { form: RoutingForm; edit: (change: (current: RoutingForm) => RoutingForm) => void }) {
  const t = useT();
  return (
    <Subsection title={t("notifyRouting.events.title")}>
      <SegmentedControl<"inherit" | "own">
        label={t("notifyRouting.events.modeLabel")}
        options={[
          { value: "inherit", label: t("notifyRouting.events.inherit") },
          { value: "own", label: t("notifyRouting.events.own") },
        ]}
        value={form.ownEvents ? "own" : "inherit"}
        onValueChange={(next) => {
          edit((current) => ({ ...current, ownEvents: next === "own" }));
        }}
        className="self-start"
      />
      <p className="text-12 text-pretty text-fg-muted">{form.ownEvents ? t("notifyRouting.events.ownHint") : t("notifyRouting.events.inheritHint")}</p>
      {form.ownEvents ? (
        <div className="flex min-w-0 flex-col gap-4">
          {EVENT_GROUPS.map((group) => (
            <EventChecklist
              key={group.id}
              id={group.id}
              kinds={group.kinds}
              values={form.events}
              onChange={(kind, next) => {
                edit((current) => ({ ...current, events: { ...current.events, [kind]: next } }));
              }}
            />
          ))}
        </div>
      ) : null}
    </Subsection>
  );
}

function EventChecklist({
  id,
  kinds,
  values,
  onChange,
}: {
  id: EventGroupId;
  kinds: readonly string[];
  values: Readonly<Record<string, boolean>>;
  onChange: (kind: string, next: boolean) => void;
}) {
  const t = useT();
  const title = t(`settings.notifications.events.groups.${id}.title`);
  const sent = kinds.filter((kind) => values[kind] === true).length;
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="flex w-full items-baseline justify-between gap-3 text-13 font-medium text-fg">
        <span>{title}</span>
        <span className="text-12 font-normal text-fg-muted tabular-nums">{t("notifyRouting.events.groupCount", { sent, total: kinds.length })}</span>
      </legend>
      {kinds.map((kind) => (
        <Checkbox
          key={kind}
          label={eventSpec(t, kind).label}
          checked={values[kind] === true}
          onCheckedChange={(next) => {
            onChange(kind, next);
          }}
        />
      ))}
    </fieldset>
  );
}

// ---------------------------------------------------------------------------------------
// Applications and severity

/** The lowest severity a route lets through; "any" is the form's word for no limit. */
type SeverityChoice = "any" | "warning" | "failed";

const LIST_FIELDS: readonly { list: RouteList; label: "appsLabel" | "projectsLabel" | "environmentsLabel" | "tagsLabel"; placeholder: "appsPlaceholder" | "projectsPlaceholder" | "environmentsPlaceholder" | "tagsPlaceholder" }[] = [
  { list: "apps", label: "appsLabel", placeholder: "appsPlaceholder" },
  { list: "projects", label: "projectsLabel", placeholder: "projectsPlaceholder" },
  { list: "environments", label: "environmentsLabel", placeholder: "environmentsPlaceholder" },
  { list: "tags", label: "tagsLabel", placeholder: "tagsPlaceholder" },
];

function RoutePart({ route, onChange }: { route: RouteForm; onChange: (route: RouteForm) => void }) {
  const t = useT();
  const narrowed = LIST_FIELDS.some(({ list }) => route[list].trim() !== "");
  const severities: readonly { value: SeverityChoice; label: string }[] = [
    { value: "any", label: t("notifyRouting.route.severity.any") },
    { value: "warning", label: t("notifyRouting.route.severity.warning") },
    { value: "failed", label: t("notifyRouting.route.severity.failed") },
  ];
  return (
    <Subsection title={t("notifyRouting.route.title")} description={t("notifyRouting.route.description")}>
      {LIST_FIELDS.map(({ list, label, placeholder }) => (
        <Field key={list} label={t(`notifyRouting.route.${label}`)} optional description={list === "tags" ? t("notifyRouting.route.tagsDescription") : t("notifyRouting.route.listDescription")}>
          <Input
            mono
            autoComplete="off"
            spellCheck={false}
            placeholder={t(`notifyRouting.route.${placeholder}`)}
            value={route[list]}
            onValueChange={(next: string) => {
              onChange({ ...route, [list]: next });
            }}
          />
        </Field>
      ))}
      {narrowed ? (
        <Checkbox
          label={t("notifyRouting.route.serverEvents")}
          description={t("notifyRouting.route.serverEventsDescription")}
          checked={route.serverEvents}
          onCheckedChange={(next) => {
            onChange({ ...route, serverEvents: next });
          }}
        />
      ) : null}
      <Field label={t("notifyRouting.route.minSeverityLabel")} nativeLabel={false}>
        <Select<SeverityChoice>
          value={route.minSeverity === "" ? "any" : route.minSeverity}
          onValueChange={(next) => {
            onChange({ ...route, minSeverity: next === "any" ? "" : next });
          }}
          options={severities}
        />
      </Field>
    </Subsection>
  );
}

// ---------------------------------------------------------------------------------------
// Quiet hours

function QuietPart({ quiet, onChange }: { quiet: QuietForm; onChange: (quiet: QuietForm) => void }) {
  const t = useT();
  const badStart = quiet.on && !isClock(quiet.start);
  const badEnd = quiet.on && !isClock(quiet.end);
  return (
    <Subsection title={t("notifyRouting.quiet.title")} description={t("notifyRouting.quiet.description")}>
      <Switch
        label={t("notifyRouting.quiet.switchLabel")}
        checked={quiet.on}
        onCheckedChange={(next) => {
          onChange({ ...quiet, on: next });
        }}
      />
      {quiet.on ? (
        <>
          <div className="grid min-w-0 gap-4 sm:grid-cols-2">
            <Field label={t("notifyRouting.quiet.startLabel")} error={badStart ? t("notifyRouting.quiet.badTime") : null}>
              <Input
                type="time"
                value={quiet.start}
                onValueChange={(next: string) => {
                  onChange({ ...quiet, start: next });
                }}
              />
            </Field>
            <Field label={t("notifyRouting.quiet.endLabel")} error={badEnd ? t("notifyRouting.quiet.badTime") : null}>
              <Input
                type="time"
                value={quiet.end}
                onValueChange={(next: string) => {
                  onChange({ ...quiet, end: next });
                }}
              />
            </Field>
          </div>
          <Field label={t("notifyRouting.quiet.timezoneLabel")} description={t("notifyRouting.quiet.timezoneDescription")}>
            <Input
              mono
              autoComplete="off"
              spellCheck={false}
              placeholder="UTC"
              value={quiet.timezone}
              onValueChange={(next: string) => {
                onChange({ ...quiet, timezone: next });
              }}
            />
          </Field>
          <Field label={t("notifyRouting.quiet.allowLabel")} nativeLabel={false}>
            <Select<QuietAllow>
              value={quiet.allow}
              onValueChange={(next) => {
                onChange({ ...quiet, allow: next });
              }}
              options={QUIET_ALLOWS.map((value) => ({ value, label: t(`notifyRouting.quiet.allow.${value}`) }))}
            />
          </Field>
          <Checkbox
            label={t("notifyRouting.quiet.digest")}
            description={t("notifyRouting.quiet.digestDescription")}
            checked={quiet.digest}
            onCheckedChange={(next) => {
              onChange({ ...quiet, digest: next });
            }}
          />
        </>
      ) : null}
    </Subsection>
  );
}
