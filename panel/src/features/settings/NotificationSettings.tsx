import { useMutation, useQuery } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { configQuery, patchConfig } from "../../api/queries/config";
import type { ConsoleConfig } from "../../api/queries/config";
import { useDocumentTitle } from "../../app/documentTitle";
import { ErrorBlock, QueryState } from "../../components/page/QueryState";
import { Sections } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { Switch } from "../../components/ui/Switch";
import { Textarea } from "../../components/ui/Textarea";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { reportActionError } from "../apps/useAppActions";
import { ChannelHeader, DirtyActions, SecretInput, TestButton, TestOutcome, useChannelTest, useRefreshConfig } from "./channelParts";
import { EmailChannel, EmailFormSkeleton } from "./EmailChannel";
import { splitErrors } from "./formErrors";
import { EVENT_KINDS, REDACTED, channelValue, channels, events, isChannelConfigured, parseHostList, publicUrlOf, readNotificationSettings } from "./notifications";
import type { ChannelSpec, NotificationSettings as Settings } from "./notifications";
import { NotificationLanguageSection } from "./NotificationLanguage";
import { configSetCommand } from "./shell";
import { SettingsFormCard, SettingsFormSkeleton, SettingsSection } from "./SettingsForm";
import { TelegramChannel } from "./TelegramChannel";
import { useSettingsForm } from "./useSettingsForm";

const SURFACE = "rounded-card border border-border bg-surface shadow-raised";

// ---------------------------------------------------------------------------------------
// The master switch

function DeliverySection({ query }: { query: ConfigQuery }) {
  const t = useT();
  const refresh = useRefreshConfig();
  const toggle = useMutation({
    mutationFn: (next: boolean) => patchConfig("notifications.enabled", next),
    onSuccess: async (_, next) => {
      await refresh();
      toast.success(next ? t("settings.notifications.delivery.turnedOn") : t("settings.notifications.delivery.turnedOff"));
    },
    onError: (error, next) => {
      reportActionError(
        next ? t("settings.notifications.delivery.turnOnFailed") : t("settings.notifications.delivery.turnOffFailed"),
        error,
      );
    },
  });
  const enabled = query.data === undefined ? undefined : readNotificationSettings(query.data.config).enabled;
  // While the write is in flight (and "Confirm it's you" is open) the switch shows where it is going.
  const checked = toggle.isPending ? toggle.variables : (enabled ?? false);
  return (
    <SettingsSection
      title={t("settings.notifications.delivery.title")}
      description={t("settings.notifications.delivery.description")}
      commands={[configSetCommand("notifications.enabled", !checked)]}
    >
      <WithSettings query={query} skeleton={<DeliverySkeleton />}>
        {() => (
          <div className={cx(SURFACE, "px-5 py-4")}>
            <Switch
              label={t("settings.notifications.delivery.switchLabel")}
              description={
                checked
                  ? t("settings.notifications.delivery.switchOnDescription")
                  : t("settings.notifications.delivery.switchOffDescription")
              }
              checked={checked}
              disabled={toggle.isPending}
              onCheckedChange={(next) => {
                toggle.mutate(next);
              }}
            />
          </div>
        )}
      </WithSettings>
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------------------
// Channels

/** One webhook-style channel: its destination, saved on its own, and a test. */
function HttpChannel({ spec, stored }: { spec: ChannelSpec; stored: Readonly<Record<string, string>> }) {
  const t = useT();
  const refresh = useRefreshConfig();
  const headingId = useId();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const test = useChannelTest(spec.id);

  const secretKeys = spec.fields.filter((field) => field.secret).map((field) => field.key);
  const dirty = spec.fields.some((field) =>
    field.secret ? (draft[field.key] ?? "").trim() !== "" : field.key in draft && draft[field.key] !== stored[field.key],
  );
  const configured = isChannelConfigured(spec, stored);

  const save = useMutation({
    mutationFn: (cleared: ReadonlySet<string>) =>
      patchConfig(`notifications.channels.${spec.id}`, channelValue(spec, stored, draft, cleared)),
    onSuccess: async (_, cleared) => {
      setDraft({});
      test.reset();
      await refresh();
      toast.success(
        cleared.size > 0
          ? t("settings.notifications.channels.removedToast", { label: spec.label })
          : t("settings.notifications.channels.savedToast", { label: spec.label }),
      );
    },
  });
  const removing = save.isPending && save.variables.size > 0;
  const errors = splitErrors(save.error, spec.fields.map((field) => field.key));

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (dirty && !save.isPending) save.mutate(new Set());
  };

  const testReason = !configured
    ? t("settings.notifications.channels.testReasonNotConfigured")
    : dirty
      ? t("settings.notifications.channels.testReasonDirty")
      : undefined;

  return (
    <article aria-labelledby={headingId} className="flex min-w-0 flex-col gap-3 px-5 py-4">
      <ChannelHeader
        id={headingId}
        label={spec.label}
        description={spec.description}
        configured={configured}
        actions={
          <>
            {/* Nothing to remove until a destination is saved. */}
            {configured || removing ? (
              <Button
                size="sm"
                variant="ghost"
                disabled={save.isPending && !removing}
                loading={removing}
                onClick={() => {
                  save.mutate(new Set(secretKeys));
                }}
              >
                {t("settings.notifications.channels.removeDestination")}
              </Button>
            ) : null}
            <TestButton test={test} disabled={dirty || !configured} {...(testReason !== undefined ? { reason: testReason } : {})} />
          </>
        }
      />
      <form noValidate onSubmit={submit} className="flex min-w-0 flex-col gap-3">
        {errors.form !== null ? (
          <ErrorBlock live compact error={errors.form} title={t("settings.notifications.channels.saveErrorTitle", { label: spec.label })} />
        ) : null}
        <div className={cx("grid min-w-0 gap-3", spec.fields.length > 1 && "sm:grid-cols-2")}>
          {spec.fields.map((field) => {
            const value = draft[field.key] ?? stored[field.key] ?? "";
            const warning = field.secret ? null : (field.warn?.(value) ?? null);
            return (
              <Field key={field.key} label={field.label} description={field.description} error={errors.fields[field.key]}>
                {field.secret ? (
                  <SecretInput
                    label={field.label}
                    placeholder={field.placeholder}
                    value={draft[field.key] ?? ""}
                    configured={stored[field.key] === REDACTED}
                    disabled={save.isPending}
                    onChange={(next) => {
                      setDraft((current) => ({ ...current, [field.key]: next }));
                    }}
                  />
                ) : (
                  <Input
                    mono
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={field.placeholder}
                    value={value}
                    disabled={save.isPending}
                    onValueChange={(next: string) => {
                      setDraft((current) => ({ ...current, [field.key]: next }));
                    }}
                  />
                )}
                {warning !== null ? (
                  <p role="alert" className="flex items-start gap-1.5 text-13 text-warn">
                    <TriangleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
                    <span>{warning}</span>
                  </p>
                ) : null}
              </Field>
            );
          })}
        </div>
        <DirtyActions
          dirty={dirty}
          pending={save.isPending && !removing}
          onDiscard={() => {
            setDraft({});
          }}
          note={t("settings.notifications.channels.testNote")}
        />
      </form>
      <div role="status" className="min-w-0 empty:hidden">
        <TestOutcome result={test.data} error={test.error} />
      </div>
    </article>
  );
}

function ChannelsSection({ query }: { query: ConfigQuery }) {
  const t = useT();
  return (
    <SettingsSection
      title={t("settings.notifications.channels.title")}
      description={t("settings.notifications.channels.description")}
    >
      <WithSettings query={query} skeleton={<ChannelsSkeleton />}>
        {(settings) => (
          <div className={cx(SURFACE, "flex min-w-0 flex-col divide-y divide-border")}>
            {channels(t).map((spec) =>
              spec.id === "email" ? (
                <EmailChannel key={spec.id} enabledStored={settings.emailEnabled} />
              ) : spec.id === "telegram" ? (
                <TelegramChannel key={spec.id} stored={settings.channels.telegram} />
              ) : (
                <HttpChannel key={spec.id} spec={spec} stored={settings.channels[spec.id]} />
              ),
            )}
          </div>
        )}
      </WithSettings>
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------------------
// Events

function EventsSection({ query }: { query: ConfigQuery }) {
  const t = useT();
  const refresh = useRefreshConfig();
  const form = useSettingsForm<Record<string, boolean>>({
    server: query.data === undefined ? undefined : readNotificationSettings(query.data.config).events,
    names: EVENT_KINDS,
    save: async (values) => {
      await patchConfig("notifications.events", Object.fromEntries(EVENT_KINDS.map((kind) => [kind, values[kind] === true])));
      await refresh();
      toast.success(t("settings.notifications.events.saved"));
    },
  });
  const values = form.values ?? {};
  return (
    <SettingsSection
      title={t("settings.notifications.events.title")}
      description={t("settings.notifications.events.description")}
      commands={
        form.dirty
          ? form.changed.map((kind) => configSetCommand(`notifications.events.${kind}`, values[kind] === true))
          : ["wasm config get notifications.events"]
      }
    >
      <WithSettings query={query} skeleton={<EventsSkeleton />}>
        {() => (
          <SettingsFormCard
            dirty={form.dirty}
            pending={form.pending}
            formError={form.formError}
            errorTitle={t("settings.notifications.events.errorTitle")}
            onSubmit={form.submit}
            onDiscard={form.discard}
          >
            <fieldset className="flex flex-col gap-3.5">
              <legend className="sr-only">{t("settings.notifications.events.legend")}</legend>
              {events(t).map((event) => (
                <Checkbox
                  key={event.kind}
                  label={event.label}
                  description={
                    event.unsent
                      ? t("settings.notifications.events.unsentSuffix", { description: event.description })
                      : event.description
                  }
                  checked={values[event.kind] === true}
                  onCheckedChange={(next) => {
                    form.set(event.kind, next);
                  }}
                />
              ))}
            </fieldset>
          </SettingsFormCard>
        )}
      </WithSettings>
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------------------
// Private destinations

function PrivateHostsSection({ query }: { query: ConfigQuery }) {
  const t = useT();
  const refresh = useRefreshConfig();
  const form = useSettingsForm({
    server:
      query.data === undefined
        ? undefined
        : { allow_private_hosts: readNotificationSettings(query.data.config).allowPrivateHosts.join("\n") },
    names: ["allow_private_hosts"],
    soleField: "allow_private_hosts",
    save: async ({ allow_private_hosts }) => {
      await patchConfig("notifications.allow_private_hosts", parseHostList(allow_private_hosts));
      await refresh();
      toast.success(t("settings.notifications.privateHosts.saved"));
    },
  });
  const typed = form.values?.allow_private_hosts ?? "";
  return (
    <SettingsSection
      title={t("settings.notifications.privateHosts.title")}
      description={t("settings.notifications.privateHosts.description")}
      // `wasm config set` stores a list as one string, so reading is the only honest command.
      commands={["wasm config get notifications.allow_private_hosts"]}
    >
      <WithSettings query={query} skeleton={<SettingsFormSkeleton fields={[{ rows: 3, description: 1 }]} />}>
        {() => (
          <SettingsFormCard
            dirty={form.dirty}
            pending={form.pending}
            formError={form.formError}
            errorTitle={t("settings.notifications.privateHosts.errorTitle")}
            onSubmit={form.submit}
            onDiscard={form.discard}
          >
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
                value={typed}
                onChange={(event) => {
                  form.set("allow_private_hosts", event.target.value);
                }}
              />
            </Field>
          </SettingsFormCard>
        )}
      </WithSettings>
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------------------
// The console's own address, for the link a deployment notification carries back to it.

function ConsoleLinkSection({ query }: { query: ConfigQuery }) {
  const t = useT();
  const refresh = useRefreshConfig();
  const form = useSettingsForm({
    server: query.data === undefined ? undefined : { public_url: publicUrlOf(query.data.config) },
    names: ["public_url"],
    soleField: "public_url",
    save: async ({ public_url }) => {
      await patchConfig("web.public_url", public_url.trim());
      await refresh();
      toast.success(t("settings.notifications.consoleLink.saved"));
    },
  });
  return (
    <SettingsSection
      title={t("settings.notifications.consoleLink.title")}
      description={t("settings.notifications.consoleLink.description")}
      commands={
        form.dirty
          ? [configSetCommand("web.public_url", form.values?.public_url ?? "")]
          : ["wasm config get web.public_url"]
      }
    >
      <QueryState query={query} label={t("settings.notifications.consoleLink.loadingLabel")} skeleton={<SettingsFormSkeleton fields={[{ description: 1 }]} />}>
        {() => (
          <SettingsFormCard
            dirty={form.dirty}
            pending={form.pending}
            formError={form.formError}
            errorTitle={t("settings.notifications.consoleLink.errorTitle")}
            onSubmit={form.submit}
            onDiscard={form.discard}
          >
            <Field
              label={t("settings.notifications.consoleLink.fieldLabel")}
              optional
              description={t("settings.notifications.consoleLink.fieldDescription")}
              error={form.fieldErrors.public_url}
            >
              <Input
                mono
                type="url"
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                placeholder="https://console.example.com"
                value={form.values?.public_url ?? ""}
                onValueChange={(value: string) => {
                  form.set("public_url", value);
                }}
                className="max-w-md"
              />
            </Field>
          </SettingsFormCard>
        )}
      </QueryState>
    </SettingsSection>
  );
}

type ConfigQuery = ReturnType<typeof useQuery<ConsoleConfig>>;

/** A section's content once the configuration is read; its skeleton or the failure until then. */
function WithSettings({
  query,
  skeleton,
  children,
}: {
  query: ConfigQuery;
  /** The loaded content's shape, so the sections below stay where they are when it arrives. */
  skeleton: ReactNode;
  children: (settings: Settings) => ReactNode;
}) {
  const t = useT();
  return (
    <QueryState query={query} label={t("settings.notifications.channels.loadingLabel")} skeleton={skeleton}>
      {(data) => children(readNotificationSettings(data.config))}
    </QueryState>
  );
}

/** The switch's card: its label and the line under it. */
function DeliverySkeleton() {
  return (
    <div aria-hidden="true" className={cx(SURFACE, "flex flex-col px-5 py-4")}>
      <div className="flex h-5 items-center justify-between gap-4">
        <Skeleton className="h-3.5 w-32" />
        <Skeleton className="h-5 w-8 rounded-pill" />
      </div>
      <div className="flex h-5 items-center">
        <Skeleton className="h-3 w-40" />
      </div>
    </div>
  );
}

/**
 * The channels as they will be drawn: their names, descriptions and field labels are fixed, so
 * they are set as the loaded view sets them (and wrap the same way); only what the configuration
 * holds - whether a channel is set up, its fields' values - is a placeholder.
 */
function ChannelsSkeleton() {
  const t = useT();
  return (
    <div aria-hidden="true" className={cx(SURFACE, "flex min-w-0 flex-col divide-y divide-border")}>
      {channels(t).map((spec) => (
        <div key={spec.id} className="flex min-w-0 flex-col gap-3 px-5 py-4">
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
            <div className="min-w-0 flex-1 basis-60">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-14 font-semibold text-fg">{spec.label}</span>
                <Skeleton className="h-3 w-24" />
              </div>
              <p className="max-w-[60ch] text-13 text-fg-muted">{spec.description}</p>
            </div>
            {/* As wide as a test button with its reason beside it. */}
            <Skeleton className="h-7 w-64 max-w-full" />
          </div>
          {spec.fields.length > 0 ? (
            <div className={cx("grid min-w-0 gap-3", spec.fields.length > 1 && "sm:grid-cols-2")}>
              {spec.fields.map((field) => (
                <div key={field.key} className="flex min-w-0 flex-col gap-1.5">
                  <span className="text-13 font-medium text-fg">{field.label}</span>
                  <Skeleton className="h-8 w-full" />
                  {field.description !== undefined ? <span className="text-12 text-fg-muted">{field.description}</span> : null}
                </div>
              ))}
            </div>
          ) : (
            // Email: the switch, then the SMTP account's form.
            <EmailFormSkeleton />
          )}
          {spec.id === "telegram" ? (
            // "Find my chat" and the line beside it.
            <div className="flex h-7 items-center gap-2">
              <Skeleton className="h-7 w-28" />
              <Skeleton className="h-3 w-72 max-w-full" />
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/** The events' card: one checkbox with its description per event, and the form's footer. */
function EventsSkeleton() {
  const t = useT();
  return (
    <div aria-hidden="true" className={cx(SURFACE, "flex min-w-0 flex-col")}>
      <div className="flex flex-col gap-3.5 p-5">
        {events(t).map((event) => (
          <div key={event.kind} className="flex items-start gap-2.5 text-14">
            <Skeleton className="mt-0.5 size-4 shrink-0" />
            <div className="flex min-w-0 flex-col">
              <span className="text-fg">{event.label}</span>
              <span className="text-13 text-fg-muted">
                {event.unsent ? t("settings.notifications.events.unsentSuffix", { description: event.description }) : event.description}
              </span>
            </div>
          </div>
        ))}
      </div>
      <div className="flex justify-end rounded-b-card border-t border-border bg-bg-sunken px-5 py-3">
        <Skeleton className="h-8 w-28" />
      </div>
    </div>
  );
}

/** Settings > Notifications: the channels alerts go to, what is sent, and a test per channel. */
export function NotificationSettings() {
  const t = useT();
  useDocumentTitle(t("settings.notifications.documentTitle"), 1);
  const query = useQuery(configQuery());
  return (
    <Sections>
      <DeliverySection query={query} />
      <ChannelsSection query={query} />
      <EventsSection query={query} />
      <NotificationLanguageSection query={query} />
      <PrivateHostsSection query={query} />
      <ConsoleLinkSection query={query} />
    </Sections>
  );
}
