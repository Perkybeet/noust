import { useMutation } from "@tanstack/react-query";
import { Check, Search, TriangleAlert } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { announce } from "../../app/Announcer";
import { findTelegramChats, patchConfig, saveTelegramSettings } from "../../api/queries/config";
import type { TelegramChat } from "../../api/queries/config";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { ChannelHeader, DirtyActions, SecretInput, TestButton, TestOutcome, useChannelTest, useRefreshConfig } from "./channelParts";
import { splitConfigErrors } from "./formErrors";
import { REDACTED, channelValue, channels, telegramChatIdWarning, telegramChatName, telegramChatType } from "./notifications";

type TelegramField = "bot_token" | "chat_id";
const FIELDS: readonly TelegramField[] = ["bot_token", "chat_id"];
const CONFIG_KEYS: Readonly<Record<string, TelegramField>> = {
  "notifications.channels.telegram.bot_token": "bot_token",
  "notifications.channels.telegram.chat_id": "chat_id",
};

/**
 * The chats the saved bot has seen, each one a click away from being the chat ID. Telegram only
 * knows a chat once someone has written in it with the bot there, so an empty answer explains
 * how to make one appear instead of just saying "none".
 */
function ChatFinder({
  tokenSaved,
  tokenTyped,
  chatId,
  onPick,
}: {
  tokenSaved: boolean;
  tokenTyped: boolean;
  chatId: string;
  onPick: (chat: TelegramChat) => void;
}) {
  const t = useT();
  const reasonId = useId();
  const find = useMutation({ mutationFn: findTelegramChats });
  const reason = !tokenSaved && !tokenTyped
    ? t("settings.notifications.telegram.findChatNeedsToken")
    : tokenTyped
      ? t("settings.notifications.telegram.findChatNeedsSavedToken")
      : undefined;
  const chats = find.data;

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Button
          size="sm"
          icon={<Search aria-hidden="true" />}
          loading={find.isPending}
          disabled={reason !== undefined}
          {...(reason !== undefined ? { "aria-describedby": reasonId } : {})}
          onClick={() => {
            find.mutate();
          }}
        >
          {t("settings.notifications.telegram.findChat")}
        </Button>
        <span id={reasonId} className="text-12 text-fg-faint">
          {reason ?? t("settings.notifications.telegram.findChatHint")}
        </span>
      </div>
      {/* The count is announced; the list itself is read on demand. */}
      <p role="status" className="sr-only">
        {chats === undefined
          ? ""
          : chats.length === 0
            ? t("settings.notifications.telegram.noChatsSeen")
            : t("settings.notifications.telegram.chatsFound", { count: chats.length })}
      </p>
      {find.isError ? <ErrorBlock compact error={find.error} title={t("settings.notifications.telegram.listFailed")} /> : null}
      {chats?.length === 0 ? (
        <div className="flex flex-col gap-1 rounded-control border border-border bg-bg-sunken px-3 py-2 text-13 text-fg-muted">
          <p className="font-medium text-fg">{t("settings.notifications.telegram.noChatsSeen")}</p>
          <p className="text-pretty">
            {t.rich("settings.notifications.telegram.noChatsHint", {
              command: (
                <code translate="no" className="mono rounded-[4px] bg-surface px-1 text-12 text-fg">
                  /start@your_bot_name
                </code>
              ),
            })}
          </p>
        </div>
      ) : null}
      {chats !== undefined && chats.length > 0 ? (
        <ul aria-label={t("settings.notifications.telegram.chatsListLabel")} className="flex min-w-0 flex-col divide-y divide-border rounded-control border border-border bg-bg-sunken">
          {chats.map((chat) => {
            const name = telegramChatName(chat, t.locale);
            const chosen = chatId.trim() === String(chat.id);
            return (
              <li key={chat.id} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 py-1.5 pr-1.5 pl-3">
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-13 text-fg" translate="no">
                    {name}
                  </span>
                  <span className="text-12 text-fg-muted">
                    {telegramChatType(chat.type, t.locale)}
                    {chat.title && chat.username ? <span translate="no">{` · @${chat.username}`}</span> : null}
                  </span>
                </div>
                <span translate="no" className="mono shrink-0 text-12 text-fg-muted">
                  {chat.id}
                </span>
                {chosen ? (
                  <span className="flex h-7 shrink-0 items-center gap-1.5 px-2 text-12 text-fg">
                    <Check aria-hidden="true" className="size-3.5 text-ok" />
                    {t("settings.notifications.telegram.chosen")}
                  </span>
                ) : (
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={t("settings.notifications.telegram.useChat", { name })}
                    onClick={() => {
                      onPick(chat);
                    }}
                  >
                    {t("settings.notifications.telegram.useButton")}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * Telegram: a bot token and the chat it posts to. Saved through PUT
 * /api/config/notifications/telegram, which refuses a chat ID Telegram would refuse and names the
 * field; an empty token keeps the stored one. Removing it blanks the token through the generic
 * PATCH, which is the one write that can.
 */
export function TelegramChannel({ stored }: { stored: Readonly<Record<string, string>> }) {
  const t = useT();
  const spec = channels(t).find((candidate) => candidate.id === "telegram");
  const refresh = useRefreshConfig();
  const headingId = useId();
  const [draft, setDraft] = useState<Partial<Record<TelegramField, string>>>({});
  const [edited, setEdited] = useState<ReadonlySet<TelegramField>>(new Set());
  // The chat ID the "missing minus" warning was last announced for, cleared by an edit. The
  // warning is drawn as the ID is typed (so it never moves the Save button under a click that
  // leaves the field) but is not a live region: its text holds the ID, so a live region would
  // be read out again on every digit. It is announced once, when the field is left with it.
  const announcedFor = useRef<string | null>(null);
  const test = useChannelTest("telegram");

  const tokenSaved = stored["bot_token"] === REDACTED;
  const typedToken = (draft.bot_token ?? "").trim();
  const chatId = draft.chat_id ?? stored["chat_id"] ?? "";
  const dirty = typedToken !== "" || (draft.chat_id !== undefined && draft.chat_id.trim() !== (stored["chat_id"] ?? ""));
  const configured = tokenSaved;

  const save = useMutation({
    mutationFn: () => saveTelegramSettings({ bot_token: typedToken, chat_id: chatId.trim() }),
    onSuccess: async () => {
      setDraft({});
      test.reset();
      await refresh();
      toast.success(t("settings.notifications.telegram.savedToast"));
    },
    onSettled: () => {
      setEdited(new Set());
    },
  });
  const remove = useMutation({
    mutationFn: () => {
      if (spec === undefined) return Promise.resolve(undefined);
      return patchConfig("notifications.channels.telegram", channelValue(spec, stored, {}, new Set(["bot_token"])));
    },
    onSuccess: async () => {
      setDraft({});
      test.reset();
      save.reset();
      await refresh();
      toast.success(t("settings.notifications.telegram.removedToast"));
    },
  });
  const pending = save.isPending || remove.isPending;
  const split = splitConfigErrors(save.error, FIELDS, CONFIG_KEYS);
  const errorOf = (name: TelegramField): string | undefined => (edited.has(name) ? undefined : split.fields[name]);

  const set = (name: TelegramField, value: string): void => {
    setDraft((current) => ({ ...current, [name]: value }));
    setEdited((current) => new Set([...current, name]));
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (dirty && !pending) save.mutate();
  };

  const testReason = !configured
    ? t("settings.notifications.channels.testReasonNotConfigured")
    : dirty
      ? t("settings.notifications.channels.testReasonDirty")
      : undefined;
  const warning = telegramChatIdWarning(chatId, t.locale);
  const tokenField = spec?.fields.find((field) => field.key === "bot_token");
  const chatField = spec?.fields.find((field) => field.key === "chat_id");

  return (
    <article aria-labelledby={headingId} className="flex min-w-0 flex-col gap-3 px-5 py-4">
      <ChannelHeader
        id={headingId}
        label={t("settings.notifications.telegram.label")}
        description={spec?.description ?? ""}
        configured={configured}
        actions={
          <>
            {configured || remove.isPending ? (
              <Button
                size="sm"
                variant="ghost"
                disabled={save.isPending}
                loading={remove.isPending}
                onClick={() => {
                  remove.mutate();
                }}
              >
                {t("settings.notifications.channels.removeDestination")}
              </Button>
            ) : null}
            <TestButton test={test} disabled={dirty || !configured} reason={testReason} />
          </>
        }
      />
      <form noValidate onSubmit={submit} className="flex min-w-0 flex-col gap-3">
        {split.form !== null ? <ErrorBlock live compact error={split.form} title={t("settings.notifications.telegram.saveErrorTitle")} /> : null}
        {remove.isError ? <ErrorBlock live compact error={remove.error} title={t("settings.notifications.telegram.removeFailed")} /> : null}
        <div className="grid min-w-0 gap-3 sm:grid-cols-2">
          <Field label={tokenField?.label ?? t("settings.notifications.telegram.botTokenLabel")} error={errorOf("bot_token")}>
            <SecretInput
              label={tokenField?.label ?? t("settings.notifications.telegram.botTokenLabel")}
              placeholder={tokenField?.placeholder ?? ""}
              value={draft.bot_token ?? ""}
              configured={tokenSaved}
              disabled={pending}
              onChange={(next) => {
                set("bot_token", next);
              }}
            />
          </Field>
          <Field label={chatField?.label ?? t("settings.notifications.telegram.chatIdLabel")} description={chatField?.description} error={errorOf("chat_id")}>
            <Input
              mono
              autoComplete="off"
              spellCheck={false}
              placeholder={chatField?.placeholder}
              value={chatId}
              disabled={pending}
              onValueChange={(next: string) => {
                announcedFor.current = null;
                set("chat_id", next);
              }}
              onBlur={() => {
                if (warning === null || announcedFor.current === chatId) return;
                announcedFor.current = chatId;
                announce(warning);
              }}
            />
            {warning !== null ? (
              <p className="flex items-start gap-1.5 text-13 text-warn">
                <TriangleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
                <span>{warning}</span>
              </p>
            ) : null}
          </Field>
        </div>
        <ChatFinder
          tokenSaved={tokenSaved}
          tokenTyped={typedToken !== ""}
          chatId={chatId}
          onPick={(chat) => {
            set("chat_id", String(chat.id));
          }}
        />
        <DirtyActions
          dirty={dirty}
          pending={save.isPending}
          onDiscard={() => {
            setDraft({});
            setEdited(new Set());
            save.reset();
          }}
          note={t("settings.notifications.channels.testNote")}
        />
      </form>
      <div role="status" className="min-w-0 empty:hidden">
        <TestOutcome result={test.data} error={test.error} source="Telegram" />
      </div>
    </article>
  );
}
