import { useMutation } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useRef, useState } from "react";

import { announce } from "../../app/Announcer";
import { findTelegramChats, patchConfig, saveTelegramSettings } from "../../api/queries/config";
import type { TelegramChat } from "../../api/queries/config";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import { ChannelDrawer, ChannelRow, SecretInput, testReasonFor, useChannelTest, useRefreshConfig } from "./channelParts";
import { splitConfigErrors } from "./formErrors";
import { REDACTED, channelValue, channels, telegramChatIdWarning, telegramChatName, telegramChatType } from "./notifications";
import { FormFailure } from "./SettingsForm";

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
function ChatFinder({ tokenSaved, tokenTyped, chatId, onPick }: { tokenSaved: boolean; tokenTyped: boolean; chatId: string; onPick: (chat: TelegramChat) => void }) {
  const t = useT();
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
          onClick={() => {
            find.mutate();
          }}
        >
          {t("settings.notifications.telegram.findChat")}
        </Button>
        <span className="text-12 text-fg-muted">{reason ?? t("settings.notifications.telegram.findChatHint")}</span>
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
          <p className="text-pretty">{t.rich("settings.notifications.telegram.noChatsHint", { command: <Mono key="command">/start@your_bot_name</Mono> })}</p>
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
                    {" · "}
                    <Mono tone="muted">{chat.id}</Mono>
                  </span>
                </div>
                {chosen ? (
                  <span className="flex h-control-sm shrink-0 items-center gap-1.5 px-2 text-12 text-fg">
                    <ICONS.copied aria-hidden="true" className="size-icon-sm" />
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
 * PATCH, which is the one write that can. A test shows Telegram's own description of a refusal.
 */
export function TelegramChannel({ stored }: { stored: Readonly<Record<string, string>> }) {
  const t = useT();
  const spec = channels(t).find((candidate) => candidate.id === "telegram");
  const refresh = useRefreshConfig();
  const test = useChannelTest("telegram");
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Partial<Record<TelegramField, string>>>({});
  const [edited, setEdited] = useState<ReadonlySet<TelegramField>>(new Set());
  // The chat ID the "missing minus" warning was last announced for, cleared by an edit. The
  // warning is drawn as the ID is typed but is not a live region: its text holds the ID, so it
  // would be read out again on every digit. It is announced once, when the field is left.
  const announcedFor = useRef<string | null>(null);

  const tokenSaved = stored["bot_token"] === REDACTED;
  const storedChat = stored["chat_id"] ?? "";
  const typedToken = (draft.bot_token ?? "").trim();
  const chatId = draft.chat_id ?? storedChat;
  const dirty = typedToken !== "" || (draft.chat_id !== undefined && draft.chat_id.trim() !== storedChat);
  const on = tokenSaved && storedChat !== "";

  const save = useMutation({
    mutationFn: async () => {
      await saveTelegramSettings({ bot_token: typedToken, chat_id: chatId.trim() });
      await refresh();
    },
    onSuccess: () => {
      setDraft({});
      test.reset();
    },
    onSettled: () => {
      setEdited(new Set());
    },
  });
  const split = splitConfigErrors(save.error, FIELDS, CONFIG_KEYS);
  const errorOf = (name: TelegramField): string | undefined => (edited.has(name) ? undefined : split.fields[name]);
  const set = (name: TelegramField, value: string): void => {
    setDraft((current) => ({ ...current, [name]: value }));
    setEdited((current) => new Set([...current, name]));
  };
  const reset = (): void => {
    setDraft({});
    setEdited(new Set());
    save.reset();
  };

  const warning = telegramChatIdWarning(chatId, t.locale);
  const tokenField = spec?.fields.find((field) => field.key === "bot_token");
  const chatField = spec?.fields.find((field) => field.key === "chat_id");
  const label = t("settings.notifications.telegram.label");

  return (
    <>
      <ChannelRow
        label={label}
        description={spec?.description ?? ""}
        on={on}
        configured={tokenSaved}
        detail={
          !tokenSaved
            ? undefined
            : storedChat === ""
              ? t("settings.notifications.telegram.noChatYet")
              : t.rich("settings.notifications.telegram.sendsTo", { chat: <Mono key="chat">{storedChat}</Mono> })
        }
        test={test}
        testSource="Telegram"
        onOpen={() => {
          setOpen(true);
        }}
      />
      <ChannelDrawer
        open={open}
        onOpenChange={setOpen}
        title={label}
        description={spec?.description ?? ""}
        dirty={dirty}
        saving={save.isPending}
        onSubmit={() => {
          save.mutate();
        }}
        onDiscard={reset}
        test={test}
        testSource="Telegram"
        testReason={testReasonFor(t, tokenSaved, dirty)}
        remove={
          tokenSaved && spec !== undefined
            ? {
                label: t("settings.notifications.channels.remove"),
                title: t("settings.notifications.channels.removeTitle", { label }),
                description: t("settings.notifications.channels.removeDescription", { label }),
                run: async () => {
                  await patchConfig("notifications.channels.telegram", channelValue(spec, stored, {}, new Set(["bot_token"])));
                  await refresh();
                  test.reset();
                },
              }
            : undefined
        }
      >
        <FormFailure error={split.form} title={t("settings.notifications.telegram.saveErrorTitle")} />
        <Field label={tokenField?.label ?? t("settings.notifications.telegram.botTokenLabel")} description={tokenField?.description} error={errorOf("bot_token")}>
          <SecretInput
            label={tokenField?.label ?? t("settings.notifications.telegram.botTokenLabel")}
            placeholder={tokenField?.placeholder ?? ""}
            value={draft.bot_token ?? ""}
            configured={tokenSaved}
            disabled={save.isPending}
            onChange={(next) => {
              set("bot_token", next);
            }}
          />
        </Field>
        <div className="flex min-w-0 flex-col gap-3">
          <Field label={chatField?.label ?? t("settings.notifications.telegram.chatIdLabel")} description={chatField?.description} error={errorOf("chat_id")}>
            <Input
              mono
              autoComplete="off"
              spellCheck={false}
              placeholder={chatField?.placeholder}
              value={chatId}
              disabled={save.isPending}
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
          </Field>
          {warning !== null ? (
            <p className="flex items-start gap-1.5 text-13 text-fg">
              <ICONS.warning aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0 text-warn" />
              <span>{warning}</span>
            </p>
          ) : null}
          <ChatFinder
            tokenSaved={tokenSaved}
            tokenTyped={typedToken !== ""}
            chatId={chatId}
            onPick={(chat) => {
              set("chat_id", String(chat.id));
            }}
          />
        </div>
      </ChannelDrawer>
    </>
  );
}
