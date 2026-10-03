/**
 * What every notification channel is built from: its row in the list, the drawer its form
 * opens in, a write-only secret field, and the test with what the channel itself answered.
 */

import { useMutation } from "@tanstack/react-query";
import type { UseMutationResult } from "@tanstack/react-query";
import { Eye, EyeOff, Send } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { testNotificationChannel } from "../../api/queries/config";
import type { NotificationTestResult } from "../../api/queries/config";
import { ErrorBlock } from "../../components/page/QueryState";
import { Subsection } from "../../components/page/Subsection";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Drawer } from "../../components/ui/Drawer";
import { IconButton } from "../../components/ui/IconButton";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { StatusGlyph, StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { T } from "../../i18n";

export { useRefreshConfig } from "./SettingsForm";

export type ChannelTest = UseMutationResult<NotificationTestResult, Error, void>;

/** The test of one channel, shared by its row and its drawer so both say what it answered. */
export function useChannelTest(channel: string): ChannelTest {
  return useMutation({ mutationFn: () => testNotificationChannel(channel) });
}

/**
 * What a test answered: the confirmation, or the failure in the receiving service's own words,
 * verbatim, under Noust's one line about it.
 *
 * @param source Who said it, as a sentence continues: "the server", "Telegram".
 */
export function TestOutcome({ result, error, source }: { result: NotificationTestResult | undefined; error: unknown; source?: string }) {
  const t = useT();
  const named = source ?? t("settings.notifications.channels.testSourceServer");
  if (error !== null && error !== undefined) {
    return <ErrorBlock compact error={error} title={t("settings.notifications.channels.testFailedTitle")} />;
  }
  if (result === undefined) return null;
  if (result.ok) {
    return (
      <p className="flex items-center gap-2 text-13 text-fg">
        <StatusGlyph state="running" className="text-ok" />
        {t("settings.notifications.channels.testSent")}
      </p>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <p className="flex items-center gap-2 text-13 font-medium text-fg">
        <StatusGlyph state="failed" className="text-fail" />
        {t("settings.notifications.channels.testFailed", { source: named })}
      </p>
      <SystemOutput
        label={t("settings.notifications.channels.testWhatSaid", { source: named })}
        maxHeight="max-h-40"
        className="rounded-control border border-border bg-bg-sunken px-3 py-2"
      >
        {result.detail}
      </SystemOutput>
    </div>
  );
}

/** A write-only field: what is typed can be shown, what is stored never comes back. */
export function SecretInput({
  label,
  placeholder,
  value,
  configured,
  onChange,
  disabled,
}: {
  /** The field's label, for the show and hide button. */
  label: string;
  placeholder: string;
  value: string;
  /** Whether a value is already stored, so the placeholder says so instead of showing the format hint. */
  configured: boolean;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  const t = useT();
  const [shown, setShown] = useState(false);
  return (
    <Input
      mono
      type={shown ? "text" : "password"}
      autoComplete="off"
      autoCapitalize="off"
      spellCheck={false}
      placeholder={configured ? t("settings.notifications.channels.secretPlaceholder") : placeholder}
      value={value}
      disabled={disabled}
      onValueChange={(next: string) => {
        onChange(next);
      }}
      suffix={
        <IconButton
          size="sm"
          label={
            shown
              ? t("settings.notifications.channels.hideSecret", { label: label.toLowerCase() })
              : t("settings.notifications.channels.showSecret", { label: label.toLowerCase() })
          }
          icon={shown ? <EyeOff /> : <Eye />}
          pressed={shown}
          onClick={() => {
            setShown((current) => !current);
          }}
        />
      }
    />
  );
}

/**
 * Sending a test: to what is saved, so it needs a destination and no unsaved change. When it
 * cannot be sent the reason is said beside it, not left to a disabled button.
 */
export function TestButton({
  test,
  reason,
  size = "sm",
  label,
}: {
  test: ChannelTest;
  reason?: string | undefined;
  size?: "sm" | "md";
  /** The accessible name when the row around it does not say which channel: "Send a test to Slack". */
  label?: string;
}) {
  const t = useT();
  const reasonId = useId();
  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <Button
        size={size}
        icon={<Send aria-hidden="true" />}
        loading={test.isPending}
        disabled={reason !== undefined}
        {...(label !== undefined ? { "aria-label": label } : {})}
        {...(reason !== undefined ? { "aria-describedby": reasonId } : {})}
        onClick={() => {
          test.mutate();
        }}
      >
        {t("settings.notifications.channels.sendTest")}
      </Button>
      {reason !== undefined ? (
        <span id={reasonId} className="text-12 text-fg-muted">
          {reason}
        </span>
      ) : null}
    </span>
  );
}

/**
 * Whether a channel sends, readable at a glance: on in the running green, off in the stopped
 * grey, each with its glyph and word (docs/DESIGN.md 6.1, owner item 56). Read-only: what
 * changes it is the channel's drawer.
 */
export function ChannelState({ on }: { on: boolean }) {
  const t = useT();
  return (
    <StatusPill
      state={on ? "running" : "stopped"}
      label={on ? t("settings.notifications.channels.on") : t("settings.notifications.channels.off")}
      size="sm"
    />
  );
}

/**
 * One channel in the list: its name and state, what it sends to, and what can be done from
 * here - set it up (or change it) in its drawer, and send a test once it has a destination.
 * The test's answer opens under the row, in the channel's own words.
 */
export function ChannelRow({
  label,
  description,
  on,
  detail,
  test,
  testSource,
  testReason,
  onOpen,
  configured,
}: {
  label: string;
  description: string;
  on: boolean;
  /** What it sends to, when that can be said: a chat ID, how many recipients. */
  detail?: ReactNode;
  test: ChannelTest;
  testSource?: string;
  testReason?: string | undefined;
  onOpen: () => void;
  configured: boolean;
}) {
  const t = useT();
  return (
    <li className="flex min-w-0 flex-col gap-3 px-4 py-2.5 sm:px-5">
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-1 basis-64 flex-col gap-0.5">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="text-13 font-medium text-fg">
              {label}
            </span>
            <ChannelState on={on} />
          </div>
          <p className="min-w-0 text-12 text-pretty text-fg-muted">{detail ?? description}</p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {configured ? <TestButton test={test} reason={testReason} label={t("settings.notifications.channels.sendTestTo", { label })} /> : null}
          <Button size="sm" aria-label={configured ? t("settings.notifications.channels.editLabel", { label }) : t("settings.notifications.channels.setUpLabel", { label })} onClick={onOpen}>
            {configured ? t("settings.notifications.channels.edit") : t("settings.notifications.channels.setUp")}
          </Button>
        </div>
      </div>
      {test.data !== undefined || test.error !== null ? (
        <div role="status" className="min-w-0">
          <TestOutcome result={test.data} error={test.error} {...(testSource !== undefined ? { source: testSource } : {})} />
        </div>
      ) : null}
    </li>
  );
}

/** A destination's value, when it is not a secret: shown in mono. */
export function Destination({ children }: { children: ReactNode }) {
  return <Mono tone="muted">{children}</Mono>;
}

/**
 * A channel's form in a drawer (480px): its fields, then a test of what is saved, and at the
 * foot Remove (when there is something to remove), Close and Save. Saving keeps the drawer
 * open, so the test that follows is one click away.
 */
export function ChannelDrawer({
  open,
  onOpenChange,
  title,
  description,
  children,
  dirty,
  saving,
  onSubmit,
  onDiscard,
  test,
  testSource,
  testReason,
  remove,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  children: ReactNode;
  dirty: boolean;
  saving: boolean;
  onSubmit: () => void;
  onDiscard: () => void;
  test: ChannelTest;
  testSource?: string;
  testReason?: string | undefined;
  /** Forgets the destination; absent when nothing is stored. */
  remove?: { label: string; title: string; description: ReactNode; run: () => Promise<void> } | undefined;
}) {
  const t = useT();
  const formId = useId();
  const [removing, setRemoving] = useState(false);
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (dirty && !saving) onSubmit();
  };
  return (
    <>
      <Drawer
        open={open}
        onOpenChange={(next) => {
          if (!next) onDiscard();
          onOpenChange(next);
        }}
        title={title}
        description={description}
        footer={
          <>
            {remove !== undefined ? (
              <Button
                variant="ghost"
                className="mr-auto"
                disabled={saving}
                onClick={() => {
                  // The question replaces the drawer: two modal layers would fight over focus.
                  onDiscard();
                  onOpenChange(false);
                  setRemoving(true);
                }}
              >
                {remove.label}
              </Button>
            ) : null}
            <Button
              variant="ghost"
              disabled={saving}
              onClick={() => {
                onDiscard();
                onOpenChange(false);
              }}
            >
              {t("settings.shared.done")}
            </Button>
            <Button type="submit" form={formId} variant={dirty ? "primary" : "secondary"} disabled={!dirty} loading={saving}>
              {t("settings.shared.save")}
            </Button>
          </>
        }
      >
        <div className="flex min-w-0 flex-col gap-6">
          <form id={formId} noValidate onSubmit={submit} className="flex min-w-0 flex-col gap-5">
            {children}
          </form>
          <Subsection title={t("settings.notifications.channels.tryTitle")} description={t("settings.notifications.channels.tryDescription")}>
            <TestButton test={test} reason={testReason} />
            <div role="status" className="min-w-0 empty:hidden">
              <TestOutcome result={test.data} error={test.error} {...(testSource !== undefined ? { source: testSource } : {})} />
            </div>
          </Subsection>
        </div>
      </Drawer>
      {remove !== undefined ? (
        <ConfirmDialog
          friction="simple"
          open={removing}
          onOpenChange={setRemoving}
          title={remove.title}
          description={remove.description}
          actionLabel={remove.label}
          onConfirm={remove.run}
        />
      ) : null}
    </>
  );
}

/** The reason a test cannot be sent now, or undefined when it can. */
export function testReasonFor(t: T, configured: boolean, dirty: boolean): string | undefined {
  if (!configured) return t("settings.notifications.channels.testReasonNotConfigured");
  if (dirty) return t("settings.notifications.channels.testReasonDirty");
  return undefined;
}
