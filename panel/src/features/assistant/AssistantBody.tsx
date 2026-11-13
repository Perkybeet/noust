import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SendHorizontal, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { assistantUsageQuery } from "../../api/queries/assistant";
import { askTurn, assistantChatKeys, beginConversation, cancelTurn, conversationsQuery, deleteConversation, rejectProposal, turnsQuery } from "../../api/queries/assistantChat";
import { useRouterState } from "@tanstack/react-router";
import { locationWord } from "../../components/assistant/AiMark";
import { UsageLine } from "../../components/assistant/UsageLine";
import type { UsageReading } from "../../components/assistant/UsageLine";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { HelpPopover } from "../../components/ui/HelpPopover";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { ServerText } from "../../components/ui/ServerText";
import { Textarea } from "../../components/ui/Textarea";
import { TextLink } from "../../components/ui/TextLink";
import { useCan } from "../../components/page/permissions";
import { announce } from "../../app/Announcer";
import { useT } from "../../i18n";
import { useServerEvent } from "../../realtime/events";
import { formatCount } from "../../lib/format";
import { useSelection } from "../../nodes/useNode";
import { applyLive, asTurn, pageBody, startLive } from "./model";
import type { LiveTurn, PageRef, Proposal, Turn } from "./model";
import { pageForPath, pageLabel } from "./pageContext";
import { approve } from "./runProposal";
import { claimRequest, usePanel } from "./store";
import { LiveView, TurnView } from "./TurnView";
import { useAssistantSession } from "./useAssistantSession";

const CONVERSATION_KEY = "noust.assistant.conversation";

function savedConversation(): string | null {
  try {
    return window.sessionStorage.getItem(CONVERSATION_KEY);
  } catch {
    return null;
  }
}

function keepConversation(id: string | null): void {
  try {
    if (id === null) window.sessionStorage.removeItem(CONVERSATION_KEY);
    else window.sessionStorage.setItem(CONVERSATION_KEY, id);
  } catch {
    // A browser that keeps nothing for a tab only forgets which conversation was open; the conversations are the server's.
  }
}

/** The page the panel reads and how it says so, with the cross that takes it away. */
function ContextLine({ page, label, onClear }: { page: PageRef | null; label: string | null; onClear: () => void }) {
  const t = useT();
  return (
    <div className="flex min-h-11 items-center gap-2 border-b border-border px-4 py-1.5 text-12">
      <OBJECT_ICONS.log aria-hidden="true" className="size-icon-sm shrink-0 text-fg-faint" />
      <p className="min-w-0 flex-1 text-fg-muted">
        {page === null || label === null ? (
          t("assistant.panel.context.none")
        ) : (
          <>
            {t("assistant.panel.context.reads")}{" "}
            <span translate="no" className="font-medium text-fg">
              {label}
            </span>
          </>
        )}
      </p>
      {page !== null ? <IconButton label={t("assistant.panel.context.stop")} icon={<X />} size="sm" tooltip={false} onClick={onClear} /> : null}
    </div>
  );
}

/** What the "?" of the panel says: it is an AI, which model and where, what it reads and who decides. */
function WhatThisIs({ model, where }: { model: string; where: string }) {
  const t = useT();
  const canData = useCan("settings.read");
  return (
    <HelpPopover label={t("assistant.panel.what")} title={t("assistant.panel.whatTitle")} side="bottom">
      <div className="flex flex-col gap-2 text-13 text-pretty text-fg-muted">
        <p>
          {t.rich("assistant.panel.whatModel", { model: <Mono>{model}</Mono>, where })}
        </p>
        <p>{t("assistant.panel.whatBody")}</p>
        {canData ? (
          <TextLink to="/settings/assistant/data" size="ui">
            {t("assistant.panel.whatLink")}
          </TextLink>
        ) : null}
      </div>
    </HelpPopover>
  );
}

function History({ onOpen, onNew }: { onOpen: (id: string) => void; onNew: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const can = useCan();
  const list = useQuery(conversationsQuery());
  const remove = useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: assistantChatKeys.list }),
  });
  const conversations = list.data ?? [];
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
      <p className="text-12 text-fg-muted">{t("assistant.panel.historyKept")}</p>
      {conversations.length === 0 ? <EmptyState variant="inline" title={t("assistant.panel.historyEmpty")} /> : null}
      <ul aria-label={t("assistant.panel.history")} className="m-0 flex list-none flex-col gap-2 p-0">
        {conversations.map((conversation) => (
          <li key={conversation.id} className="flex items-center gap-2">
            <Button variant="secondary" className="min-w-0 flex-1 justify-start" onClick={() => onOpen(conversation.id)}>
              <span className="min-w-0 truncate">{conversation.title === "" ? t("assistant.panel.untitled") : conversation.title}</span>
            </Button>
            <IconButton label={t("assistant.panel.deleteConversation", { title: conversation.title === "" ? t("assistant.panel.untitled") : conversation.title })} icon={<ICONS.delete />} size="sm" onClick={() => (can("assistant.use") ? remove.mutate(conversation.id) : undefined)} />
          </li>
        ))}
      </ul>
      <div>
        <Button variant="secondary" size="sm" icon={<ICONS.add aria-hidden="true" />} onClick={onNew}>
          {t("assistant.panel.newConversation")}
        </Button>
      </div>
    </div>
  );
}

/**
 * The body of the panel, the same in the three places it lives: what it reads (and the cross that takes it away), the conversation, and the
 * box where a question is typed with how much of its limits the conversation has used and the line that says it is an AI. A question is a
 * turn the server answers at once and writes while it works: what it reads, its words and what it proposes arrive on the event stream.
 * Carrying a proposal out is the person's, with their own session, through the same call the console would make.
 */
export function AssistantBody({ docked, onClose }: { docked: boolean; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const session = useAssistantSession();
  const panel = usePanel();
  const selection = useSelection();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const can = useCan();
  const [conversation, setConversation] = useState<string | null>(savedConversation);
  const [history, setHistory] = useState(false);
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [asked, setAsked] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  // Taking the page away holds for this page and this request only: another page, or another "Explain this", reads again.
  const [dismissedFor, setDismissedFor] = useState<string | null>(null);
  const log = useRef<HTMLDivElement>(null);

  const server = selection.kind === "all" ? "*" : selection.node;
  const requested = panel.request;
  const context = `${pathname}|${String(requested?.nonce ?? 0)}`;
  const dismissed = dismissedFor === context;
  const page = requested !== null && requested.page !== null ? requested.page : dismissed ? null : pageForPath(pathname);
  const label = page === null ? null : requested?.page !== null && requested?.label !== null && requested?.label !== undefined ? requested.label : pageLabel(t, page);

  const turnsQ = useQuery({ ...turnsQuery(conversation ?? ""), enabled: conversation !== null });
  const turns: Turn[] = useMemo(() => (turnsQ.data?.turns ?? []).flatMap((turn) => asTurn(turn) ?? []), [turnsQ.data]);
  const usage = useQuery({ ...assistantUsageQuery(conversation), enabled: session !== null });
  const fleet = selection.kind === "all" || selection.node !== null;

  // Following the stream: what the server says of the turn while it works, to this account's streams and no other.
  useServerEvent("assistant", (event) => {
    if (event["conversation"] !== conversation) return;
    if (event["type"] === "start") {
      setLive(startLive(event));
      return;
    }
    if (event["type"] === "end") {
      void queryClient.invalidateQueries({ queryKey: assistantChatKeys.turns(String(event["conversation"])) }).then(() => setLive(null));
      void queryClient.invalidateQueries({ queryKey: assistantUsageQuery(conversation).queryKey });
      announce(t("assistant.panel.announce.done"));
      return;
    }
    if (event["type"] === "proposal" && event["turn"] === 0) {
      void queryClient.invalidateQueries({ queryKey: assistantChatKeys.turns(String(event["conversation"])) });
      return;
    }
    setLive((current) => {
      if (current === null || event["turn"] !== current.id) return current;
      const next = applyLive(current, event);
      if (next === "end") return current;
      if (event["type"] === "step" && next.steps.length > current.steps.length) {
        const step = next.steps.at(-1);
        if (step?.what !== null && step?.what !== undefined) announce(step.what.text);
      }
      return next;
    });
  });

  const ask = useMutation({
    // The box is emptied and the conversation is shown the moment a question is sent, whoever sent it.
    onMutate: () => {
      setText("");
      setHistory(false);
    },
    mutationFn: async (message: string) => {
      let id = conversation;
      if (id === null) {
        const created = await beginConversation();
        id = created.id;
        setConversation(id);
        keepConversation(id);
      }
      setAsked(message);
      const started = await askTurn(id, { message, ...(pageBody(page, null) !== null ? { page: pageBody(page, null) } : {}), ...(server !== null ? { server } : {}), locale: t.locale });
      return { id, started };
    },
    onSuccess: ({ id, started }) => {
      setLive((current) => current ?? { id: started.turn, model: started.model, location: started.location, server: started.server, draft: "", steps: [], proposals: [], warnings: [], usage: null });
      void queryClient.invalidateQueries({ queryKey: assistantChatKeys.turns(id) });
    },
  });

  const send = useCallback(
    (message: string): void => {
      const clean = message.trim();
      if (clean === "" || !can("assistant.use")) return;
      ask.mutate(clean);
    },
    [ask, can],
  );

  // "Explain this" and the palette open the panel with a question already asked; the person's own questions go through the box. A
  // request is answered once, even when the panel is drawn again in another place (beside the page, then over it on a narrower window).
  useEffect(() => {
    if (requested === null || !claimRequest(requested.nonce)) return;
    if (requested.question !== undefined) send(requested.question);
  }, [requested, send]);

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [turns.length, live?.steps.length, live?.draft]);

  const stop = useMutation({ mutationFn: () => (conversation !== null && live !== null ? cancelTurn(conversation, live.id) : Promise.resolve({ cancelled: false })) });
  const decide = useMutation({
    mutationFn: async ({ proposal, verdict }: { proposal: Proposal; verdict: "approve" | "reject" }) => {
      if (conversation === null) return;
      setBusy(proposal.id);
      if (verdict === "approve") await approve(conversation, proposal);
      else await rejectProposal(conversation, proposal.id);
    },
    onSettled: async () => {
      setBusy(null);
      if (conversation !== null) await queryClient.invalidateQueries({ queryKey: assistantChatKeys.turns(conversation) });
    },
  });

  // Stopping and deciding are the person's own, in their own conversation: the permission that opens the panel.
  const stopTurn = (): void => {
    if (can("assistant.use")) stop.mutate();
  };
  const decideProposal = (proposal: Proposal, verdict: "approve" | "reject"): void => {
    if (can("assistant.use")) decide.mutate({ proposal, verdict });
  };

  if (session === null) return null;

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send(text);
    }
  };

  const model = session.model ?? "";
  const where = locationWord(t, session.location);
  const limits = usage.data?.limits ?? {};
  const mine = usage.data?.conversation;
  const readings: UsageReading[] =
    usage.data === undefined
      ? []
      : [
          {
            id: "tokens",
            label: t("assistant.usage.tokens"),
            used: mine?.prompt_tokens ?? 0,
            cap: limits["input_tokens_per_conversation"] ?? 0,
            valueText: t("assistant.usage.tokensValue", { used: formatCount(mine?.prompt_tokens ?? 0, t.locale), cap: formatCount(limits["input_tokens_per_conversation"] ?? 0, t.locale) }),
          },
          ...(usage.data.priced_models.includes(model)
            ? [{ id: "spend", label: t("assistant.usage.spend"), used: mine?.spend ?? 0, cap: limits["spend_per_conversation"] ?? 0, valueText: `${String(Math.round((mine?.spend ?? 0) * 1000) / 1000)} / ${String(limits["spend_per_conversation"] ?? 0)} ${usage.data.currency}` }]
            : []),
          { id: "steps", label: t("assistant.usage.steps"), used: live?.usage?.steps ?? 0, cap: limits["steps_per_turn"] ?? 0, valueText: t("assistant.usage.stepsValue", { used: live?.usage?.steps ?? 0, cap: limits["steps_per_turn"] ?? 0 }), onlyWhenNear: true },
        ];

  return (
    <div className="flex h-full min-h-0 flex-col" data-frame-body="assistant">
      <div className="flex min-h-11 shrink-0 items-center gap-1 border-b border-border px-2">
        {docked ? <OBJECT_ICONS.assistant aria-hidden="true" className="ml-2 size-icon-md shrink-0 text-fg-muted" /> : null}
        {docked ? <p className="title min-w-0 flex-1 truncate pl-1 text-14 text-fg">{history ? t("assistant.panel.history") : t("assistant.name")}</p> : <span className="flex-1" />}
        <WhatThisIs model={model} where={where} />
        <IconButton label={t("assistant.panel.newConversation")} icon={<ICONS.add />} size="sm" tooltip={false} onClick={() => { setConversation(null); keepConversation(null); setLive(null); setHistory(false); }} />
        <IconButton label={history ? t("assistant.panel.backToConversation") : t("assistant.panel.history")} icon={<OBJECT_ICONS.activity />} size="sm" tooltip={false} pressed={history} onClick={() => setHistory((current) => !current)} />
        {docked ? <IconButton label={t("assistant.panel.close")} icon={<ICONS.close />} size="sm" tooltip={false} onClick={onClose} /> : null}
      </div>
      {history ? (
        <History
          onOpen={(id) => {
            setConversation(id);
            keepConversation(id);
            setLive(null);
            setHistory(false);
          }}
          onNew={() => {
            setConversation(null);
            keepConversation(null);
            setLive(null);
            setHistory(false);
          }}
        />
      ) : (
        <>
          <ContextLine page={page} label={label} onClear={() => setDismissedFor(context)} />
          {fleet ? <p className="border-b border-border px-4 py-1.5 text-12 text-fg-muted">{server === "*" ? t("assistant.panel.server.all") : t("assistant.panel.server.one", { server: server ?? "" })}</p> : null}
          <div ref={log} className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto p-4">
            {!session.tools ? (
              <InlineMessage tone="info" trailing={<ICONS.locked aria-hidden="true" className="size-icon-sm text-fg-faint" />}>
                {t("assistant.panel.onlyExplains")}
              </InlineMessage>
            ) : null}
            {turns.length === 0 && live === null ? <EmptyState variant="inline" title={t("assistant.panel.empty")} /> : null}
            {turns
              .filter((turn) => live?.id !== turn.id)
              .map((turn) => (
                <TurnView
                  key={turn.id}
                  turn={turn}
                  fleet={fleet}
                  busy={busy}
                  onApprove={(proposal) => decideProposal(proposal, "approve")}
                  onReject={(proposal) => decideProposal(proposal, "reject")}
                  onAskAgain={send}
                />
              ))}
            {live !== null ? <LiveView live={live} question={asked} fleet={fleet} onStop={stopTurn} /> : null}
            {ask.isError ? <ErrorBlock compact live error={ask.error} title={t("assistant.panel.askFailed")} /> : null}
            {decide.isError ? <ErrorBlock compact live error={decide.error} title={t("assistant.panel.decideFailed")} /> : null}
          </div>
          <div className="flex shrink-0 flex-col gap-2 border-t border-border p-3">
            <UsageLine
              readings={readings}
              notes={(usage.data?.notes ?? []).map((note) => <ServerText key={note.code} coded={note} text={note.text} />)}
              {...(can("settings.manage") ? { change: <TextLink to="/settings/assistant/data" size="ui">{t("assistant.usage.change")}</TextLink> } : {})}
            />
            <div className="flex items-end gap-2">
              <Textarea
                aria-label={t("assistant.panel.composer.label")}
                placeholder={t("assistant.panel.composer.placeholder")}
                rows={2}
                value={text}
                onChange={(event) => setText(event.target.value)}
                onKeyDown={onKeyDown}
                className="min-w-0 flex-1"
              />
              <IconButton label={t("assistant.panel.composer.send")} icon={<SendHorizontal />} variant="secondary" tooltip={false} disabled={ask.isPending || live !== null} onClick={() => send(text)} />
            </div>
            <p className="text-12 text-pretty text-fg-muted">{t("assistant.panel.composer.foot")}</p>
          </div>
        </>
      )}
    </div>
  );
}

