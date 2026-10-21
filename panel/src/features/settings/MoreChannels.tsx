/**
 * Channels added by name: a second Slack for staging, a Pushover for whoever is on call. They are
 * what `notifications.channels` holds besides the nine default ones, each with its own settings,
 * events, routing and quiet hours. Adding one asks only for a name and a type; its destination is
 * set up next, in the same drawer a default channel uses.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { notificationChannelsQuery, saveNotificationChannel } from "../../api/queries/notifications";
import type { NotificationChannel } from "../../api/queries/notifications";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useRefreshConfig } from "./channelParts";
import { HttpChannel } from "./HttpChannel";
import { channels } from "./notifications";
import type { ChannelSpec } from "./notifications";
import { FormFailure } from "./SettingsForm";

/** How a channel may be named: the server's own rule (noust.core.notifications.instances.NAME_PATTERN). */
const NAME_SHAPE = /^[a-z][a-z0-9_-]{0,31}$/;

/**
 * The spec an added channel is edited with. An email channel has no credentials of its own: it
 * is told whom to write to and sends through the Email channel's SMTP account.
 */
function specOf(t: T, base: ChannelSpec): ChannelSpec {
  if (base.id !== "email") return base;
  return {
    ...base,
    fields: [
      {
        key: "recipients",
        label: t("notifyRouting.more.recipientsLabel"),
        secret: false,
        destination: true,
        placeholder: "ops@example.com, oncall@example.com",
        description: t("notifyRouting.more.recipientsDescription"),
      },
    ],
  };
}

/** What a channel holds, as the strings a form shows: a list is written the way it is typed. */
function storedOf(spec: ChannelSpec, settings: NotificationChannel["settings"]): Record<string, string> {
  return Object.fromEntries(
    spec.fields.map((field) => {
      const value = settings?.[field.key];
      const text = typeof value === "string" ? value : typeof value === "number" ? String(value) : Array.isArray(value) ? value.join(", ") : "";
      return [field.key, text];
    }),
  );
}

function AddedChannel({ channel, openAtStart }: { channel: NotificationChannel; openAtStart: boolean }) {
  const t = useT();
  const base = channels(t).find((candidate) => candidate.id === channel.type);
  if (base === undefined) return null;
  const spec = specOf(t, base);
  return (
    <HttpChannel
      spec={spec}
      stored={storedOf(spec, channel.settings)}
      name={channel.name}
      label={channel.name}
      description={t("notifyRouting.more.typeOf", { label: base.label })}
      added
      openAtStart={openAtStart}
    />
  );
}

function AddChannelDrawer({ open, onOpenChange, onAdded }: { open: boolean; onOpenChange: (open: boolean) => void; onAdded: (name: string) => void }) {
  const t = useT();
  const refresh = useRefreshConfig();
  const formId = useId();
  const [name, setName] = useState("");
  const [type, setType] = useState("slack");
  // Whether the name was pressed through while it was not one: said beside the box, not by a dead button.
  const [named, setNamed] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const types = channels(t);
  const valid = NAME_SHAPE.test(name.trim());

  const add = useMutation({
    mutationFn: async () => {
      const chosen = name.trim();
      await saveNotificationChannel(chosen, { type });
      // Before the list is read again, so the row it brings opens its own drawer as it appears.
      onAdded(chosen);
      await refresh();
    },
    onSuccess: () => {
      setName("");
      onOpenChange(false);
    },
  });
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (add.isPending) return;
    if (!valid) {
      setNamed(true);
      nameRef.current?.focus();
      return;
    }
    add.mutate();
  };

  return (
    <Drawer
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setName("");
          setNamed(false);
          add.reset();
        }
        onOpenChange(next);
      }}
      title={t("notifyRouting.more.addTitle")}
      description={t("notifyRouting.more.addDescription")}
      footer={
        <>
          <Button
            variant="ghost"
            disabled={add.isPending}
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={add.isPending}>
            {t("notifyRouting.more.create")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex min-w-0 flex-col gap-5">
        <FormFailure error={add.error} title={t("notifyRouting.more.createErrorTitle")} />
        <Field label={t("notifyRouting.more.nameLabel")} description={t("notifyRouting.more.nameDescription")} error={named && !valid ? t("notifyRouting.more.nameBad") : undefined}>
          <Input
            ref={nameRef}
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="slack-staging"
            value={name}
            onValueChange={(next: string) => {
              setName(next);
              setNamed(false);
            }}
          />
        </Field>
        <Field label={t("notifyRouting.more.typeLabel")} nativeLabel={false}>
          <Select value={type} onValueChange={setType} options={types.map((spec) => ({ value: spec.id, label: spec.label }))} />
        </Field>
      </form>
    </Drawer>
  );
}

/** The channels added by name, and the way to add one. Absent on a server that predates them. */
export function MoreChannels() {
  const t = useT();
  const list = useQuery(notificationChannelsQuery());
  const [adding, setAdding] = useState(false);
  const [justAdded, setJustAdded] = useState<string | null>(null);

  // A server whose Noust predates channels by name has nothing to show here, and says so nowhere else.
  // design-exception: hand-error-ternary an older server has no such channels: that is not a failure to load
  if (list.isError && list.data === undefined) return null;
  const added = list.data?.items.filter((channel) => !channel.default) ?? [];

  return (
    <Section
      title={t("notifyRouting.more.title")}
      description={t("notifyRouting.more.description")}
      actions={
        list.data === undefined ? undefined : (
          <Button
            icon={<Plus aria-hidden="true" />}
            onClick={() => {
              setAdding(true);
            }}
          >
            {t("notifyRouting.more.add")}
          </Button>
        )
      }
    >
      <Card padding="none">
        {list.data === undefined ? (
          <LoadingRegion label={t("notifyRouting.more.loading")}>
            <div aria-hidden="true" className="flex items-center justify-between gap-4 px-4 py-3 sm:px-5">
              <Skeleton className="h-4 w-64 max-w-full" />
              <Skeleton className="h-control-sm w-20" />
            </div>
          </LoadingRegion>
        ) : added.length === 0 ? (
          <EmptyState variant="inline" title={t("notifyRouting.more.empty")} className="px-4 sm:px-5" />
        ) : (
          <ul aria-label={t("notifyRouting.more.listLabel")} className="flex min-w-0 flex-col divide-y divide-border">
            {added.map((channel) => (
              <AddedChannel key={channel.name} channel={channel} openAtStart={channel.name === justAdded && !channel.configured} />
            ))}
          </ul>
        )}
      </Card>
      <AddChannelDrawer open={adding} onOpenChange={setAdding} onAdded={setJustAdded} />
    </Section>
  );
}
