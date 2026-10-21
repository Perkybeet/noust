/**
 * A channel that is a destination and some credentials: Webhook, Slack, Discord, Pushover, ntfy,
 * Gotify and Teams, and any channel added by name (a second Slack for staging). Its fields come
 * from the channel's spec, so each type is data and not another component. Write-only: what is
 * stored never comes back, and a secret left alone is sent as "***", which keeps the stored one.
 */

import { useMutation } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState } from "react";

import { removeNotificationChannel, saveNotificationChannel } from "../../api/queries/notifications";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { CopyValue } from "../../components/ui/CopyValue";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";
import { ChannelDrawer, ChannelRow, SecretInput, SecretTextarea, testReasonFor, useChannelTest, useRefreshConfig } from "./channelParts";
import { REDACTED, channelValue, isChannelConfigured } from "./notifications";
import type { ChannelSpec } from "./notifications";
import { FormFailure } from "./SettingsForm";

/** 32 random bytes in hex: a signing secret nobody typed. */
function newSecret(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function fieldError(error: unknown, key: string): string | undefined {
  if (error === null || error === undefined || typeof error !== "object") return undefined;
  const fields = (error as { fields?: Record<string, string> | null }).fields;
  return fields?.[key];
}

function hasFieldError(error: unknown, spec: ChannelSpec): boolean {
  return spec.fields.some((field) => fieldError(error, field.key) !== undefined);
}

export interface HttpChannelProps {
  spec: ChannelSpec;
  /** What the channel holds now: secrets as "***", everything else in clear. */
  stored: Readonly<Record<string, string>>;
  /** The channel's name on the server; the type's own name for a default channel. */
  name?: string;
  /** What the row is called; the type's name for a default channel. */
  label?: string;
  description?: string;
  /**
   * The channel was added by name. It is removed outright; a default channel is only emptied.
   */
  added?: boolean;
  /** Open the drawer as soon as the row appears: the channel was just added and has no destination yet. */
  openAtStart?: boolean;
}

export function HttpChannel({ spec, stored, name = spec.id, label = spec.label, description = spec.description, added = false, openAtStart = false }: HttpChannelProps) {
  const t = useT();
  const refresh = useRefreshConfig();
  const test = useChannelTest(name);
  const [open, setOpen] = useState(openAtStart);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [cleared, setCleared] = useState<ReadonlySet<string>>(new Set());
  const [generated, setGenerated] = useState<string | null>(null);

  const configured = isChannelConfigured(spec, stored);
  // A secret is write-only, so any text typed is a change; a plain field is one only when it differs from what is stored.
  const dirty =
    cleared.size > 0 ||
    spec.fields.some((field) => (field.secret ? (draft[field.key] ?? "").trim() !== "" : draft[field.key] !== undefined && draft[field.key]?.trim() !== (stored[field.key] ?? "")));

  /**
   * One write of the channel's settings. Always through the channel API, which changes only the
   * settings it is given: writing the block through the configuration would replace it whole and
   * lose the channel's routing, quiet hours, cap and summary.
   */
  const write = async (values: Record<string, string>): Promise<void> => {
    await saveNotificationChannel(name, { type: spec.id, settings: values });
    await refresh();
  };
  const save = useMutation({
    mutationFn: (forget: ReadonlySet<string>) => write(channelValue(spec, stored, draft, forget)),
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
        name={name}
        label={label}
        description={description}
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
        title={label}
        description={description}
        dirty={dirty}
        saving={save.isPending}
        onSubmit={() => {
          save.mutate(cleared);
        }}
        onDiscard={reset}
        test={test}
        testReason={testReasonFor(t, configured, dirty)}
        remove={
          added
            ? {
                label: t("notifyRouting.more.remove"),
                title: t("notifyRouting.more.removeTitle", { name }),
                description: t("notifyRouting.more.removeDescription", { name }),
                run: async () => {
                  await removeNotificationChannel(name);
                  await refresh();
                },
              }
            : configured
              ? {
                  label: t("settings.notifications.channels.remove"),
                  title: t("settings.notifications.channels.removeTitle", { label }),
                  description: t("settings.notifications.channels.removeDescription", { label }),
                  run: async () => {
                    await write(channelValue(spec, stored, {}, destinationKeys));
                    test.reset();
                  },
                }
              : undefined
        }
      >
        <FormFailure error={save.error !== null && !hasFieldError(save.error, spec) ? save.error : null} title={t("settings.notifications.channels.saveErrorTitle", { label })} />
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
                {field.secret && field.multiline === true ? (
                  <SecretTextarea
                    label={field.label}
                    placeholder={field.placeholder}
                    value={draft[field.key] ?? ""}
                    configured={isStored && !forgetting}
                    disabled={save.isPending || forgetting}
                    onChange={(next) => {
                      setDraft((current) => ({ ...current, [field.key]: next }));
                    }}
                  />
                ) : field.secret ? (
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
                ) : (
                  <Input
                    mono
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={field.placeholder}
                    value={draft[field.key] ?? stored[field.key] ?? ""}
                    disabled={save.isPending}
                    onValueChange={(next: string) => {
                      setDraft((current) => ({ ...current, [field.key]: next }));
                    }}
                  />
                )}
              </Field>
              {generated !== null && field.key === "secret" && draft["secret"] === generated ? (
                <CopyValue value={generated} label={t("settings.notifications.channels.webhook.secretCopyLabel")} />
              ) : null}
              {generated !== null && field.key === "secret" && draft["secret"] === generated ? (
                <p className="text-12 text-fg-muted">{t("settings.notifications.channels.webhook.generatedHint")}</p>
              ) : null}
              {field.optional === true && field.secret && isStored ? (
                <Checkbox
                  label={spec.id === "webhook" && field.key === "secret" ? t("settings.notifications.channels.webhook.stopSigning") : t("settings.notifications.channels.forgetField", { label: field.label.toLowerCase() })}
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
