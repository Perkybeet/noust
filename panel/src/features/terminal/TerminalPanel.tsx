import { useQuery } from "@tanstack/react-query";
import { LogOut, Plus, SquareTerminal } from "lucide-react";
import { useEffect, useState } from "react";

import { announce } from "../../app/Announcer";
import { useNow } from "../../components/page/clock";
import { ErrorBlock, QueryState } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { ICONS } from "../../components/ui/icons";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Popover } from "../../components/ui/Popover";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { TerminalScreen } from "../../components/ui/TerminalScreen";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import type { Injected } from "./useTerminalSession";
import { useTerminalSession } from "./useTerminalSession";
import type { Phase, SessionState, TerminalTarget } from "./useTerminalSession";
import { terminalStatusQuery, useHasPermission } from "./queries";
import type { TerminalStatus } from "./queries";
import { clock, endedSentence, secondsUntil } from "./words";
import type { ReactNode } from "react";

/** The commands that turn the server's shell on, verbatim: the ones the server's own refusal names. */
export const SERVER_ENABLE_COMMAND = "noust config set terminal.server.enabled true";
export const SERVER_FORCE_COMMAND = "noust config set terminal.server.force_ens true";
export const TERMINALS_ENABLE_COMMAND = "noust config set terminal.enabled true";

const PHASE_STATUS: Record<Phase, Status> = {
  idle: "unknown",
  opening: "deploying",
  open: "running",
  reconnecting: "warning",
  ended: "stopped",
  failed: "failed",
};

const DEFAULT_SHELL = "default";

export interface TerminalPanelProps {
  target: TerminalTarget;
  /** For tests: how the socket is made and where the console's ticket comes from. */
  injected?: Injected;
}

function minutes(seconds: number): number {
  return Math.max(1, Math.round(seconds / 60));
}

/** The strip above a running terminal: where it is, as whom, and when it ends. */
function SessionHeader({ state, t }: { state: SessionState; t: T }) {
  const now = useNow(() => 1000);
  const { info } = state;
  const left = info !== null && state.phase === "open" ? secondsUntil(info.endsAt, now) : null;
  return (
    <div role="group" aria-label={t("terminal.header.label")} className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 text-13 text-fg-muted">
      <StatusPill state={PHASE_STATUS[state.phase]} label={t(`terminal.state.${state.phase === "idle" ? "ended" : state.phase}`)} />
      {info !== null ? (
        <>
          <span className="flex min-w-0 items-center gap-1.5">
            <Mono tone="default" truncate>
              {info.target.label}
            </Mono>
            <span>{t("terminal.header.as")}</span>
            <Mono tone="default">{info.target.account}</Mono>
          </span>
          {left !== null ? <span>{t("terminal.header.endsIn", { time: clock(left) })}</span> : null}
          <span>{t("terminal.header.idleLimit", { minutes: minutes(info.idleSeconds) })}</span>
          {info.recorded ? <span>{t("terminal.header.recorded")}</span> : null}
        </>
      ) : null}
    </div>
  );
}

/**
 * What is said under the screen while a limit is near (a framed condition that asks for a key
 * press), and after the connection came back (a line: it asks for nothing).
 */
function Notices({ state, t }: { state: SessionState; t: T }) {
  const now = useNow(() => 1000);
  const { notice } = state;
  // A limit's warning is said once, when it arrives; the visible count below is not a live region.
  useEffect(() => {
    if (notice === null) return;
    const seconds = Math.max(0, Math.ceil((notice.at - Date.now()) / 1000));
    announce(t(notice.kind === "idle" ? "terminal.notice.idle" : "terminal.notice.limit", { seconds }), "polite");
  }, [notice, t]);
  if (state.phase === "ended" || state.phase === "failed") return null;
  return (
    <>
      {notice !== null ? (
        <Notice tone="warning" variant="inline">
          {t(notice.kind === "idle" ? "terminal.notice.idle" : "terminal.notice.limit", { seconds: secondsUntil(notice.at, now) })}
        </Notice>
      ) : null}
      {state.phase === "open" && state.lost ? <InlineMessage>{t("terminal.notice.lost")}</InlineMessage> : null}
      {state.phase === "open" && state.resumed ? <InlineMessage tone="info">{t("terminal.notice.resumed")}</InlineMessage> : null}
    </>
  );
}

/** Why there is no terminal here, when the server has them off. */
function Off({ target, status, t }: { target: TerminalTarget; status: TerminalStatus; t: T }) {
  if (target.kind === "server") {
    const forced = status.server.enabled && status.ens;
    return (
      <EmptyState
        variant="firstUse"
        icon={<SquareTerminal />}
        title={t("terminal.off.title")}
        description={
          <>
            {t("terminal.off.description")} {forced ? `${t("terminal.off.ens")} ${t("terminal.off.force")}` : t("terminal.off.enable")}
          </>
        }
        command={forced ? SERVER_FORCE_COMMAND : SERVER_ENABLE_COMMAND}
      />
    );
  }
  return (
    <EmptyState
      variant="firstUse"
      icon={<SquareTerminal />}
      title={t("terminal.disabled.title")}
      description={`${t("terminal.disabled.description")} ${t("terminal.disabled.enable")}`}
      command={TERMINALS_ENABLE_COMMAND}
    />
  );
}

interface StartProps {
  target: TerminalTarget;
  status: TerminalStatus;
  permitted: boolean | undefined;
  loading: boolean;
  onOpen: (options: { shell?: string; service?: string }) => void;
  t: T;
}

/**
 * How a session works, said once and kept out of the way: what the shell is, what is kept of it
 * and when it ends, and that opening it asks to confirm it's you. Three paragraphs behind one
 * button, where they were three paragraphs above the one that opens the terminal.
 */
function HowItWorks({ target, status, t }: { target: TerminalTarget; status: TerminalStatus; t: T }) {
  const app = target.kind === "app";
  return (
    <Popover
      trigger={
        <Button size="sm" variant="ghost" icon={<ICONS.info aria-hidden="true" />}>
          {t("terminal.start.how")}
        </Button>
      }
      title={t("terminal.start.how")}
      side="bottom"
      align="start"
      className="w-96"
    >
      <div className="flex flex-col gap-2 text-13 text-fg-muted">
        <p>{app ? t("terminal.start.app.description") : t("terminal.start.server.description")}</p>
        <p>{t("terminal.start.recorded", { idle: minutes(status.idle_seconds), max: minutes(status.max_seconds) })}</p>
        <p>{t("terminal.start.sudo")}</p>
      </div>
    </Popover>
  );
}

/** Before the session opens: its state and the button on one line, and what is behind them one press away. */
function Start({ target, status, permitted, loading, onOpen, t }: StartProps) {
  const [shell, setShell] = useState(DEFAULT_SHELL);
  const [service, setService] = useState("");
  const app = target.kind === "app";
  const shells = [
    { value: DEFAULT_SHELL, label: t("terminal.actions.shellDefault") },
    ...status.shells.map((name) => ({ value: name, label: name })),
  ];
  return (
    <Card
      title={app ? t("terminal.start.app.title", { domain: target.domain }) : t("terminal.start.server.title")}
      status={<StatusPill state="unknown" label={t("terminal.state.notOpen")} size="sm" />}
      description={app ? t("terminal.start.app.summary") : t("terminal.start.server.summary")}
    >
      <div className="flex flex-col gap-3">
        {permitted === false ? <Notice tone="warning">{t("terminal.start.permission", { permission: app ? "apps.terminal" : "server.terminal" })}</Notice> : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            // The application's header already carries the page's primary action (Update).
            variant={app ? "secondary" : "primary"}
            icon={<SquareTerminal aria-hidden="true" />}
            loading={loading}
            disabled={permitted !== true}
            onClick={() => {
              onOpen({
                ...(shell !== DEFAULT_SHELL ? { shell } : {}),
                ...(app && service.trim() !== "" ? { service: service.trim() } : {}),
              });
            }}
          >
            {loading ? t("terminal.actions.opening") : t("terminal.actions.open")}
          </Button>
          <Popover
            trigger={
              <Button size="sm" variant="ghost">
                {t("terminal.actions.options")}
              </Button>
            }
            title={t("terminal.actions.options")}
            side="bottom"
            align="start"
          >
            <div className="flex flex-col gap-4">
              <Field label={t("terminal.actions.shell")} nativeLabel={false}>
                <Select options={shells} value={shell} onValueChange={setShell} />
              </Field>
              {app ? (
                <Field label={t("terminal.actions.service")} description={t("terminal.actions.serviceHelp")} optional>
                  <Input mono value={service} onChange={(event) => setService(event.currentTarget.value)} />
                </Field>
              ) : null}
            </div>
          </Popover>
          <HowItWorks target={target} status={status} t={t} />
        </div>
      </div>
    </Card>
  );
}

/** How the screen is shown: a view of the session, not a setting, so two checkboxes in a popover of the bar. */
function DisplayOptions({
  textView,
  onTextView,
  announceOutput,
  onAnnounce,
  t,
}: {
  textView: boolean;
  onTextView: (next: boolean) => void;
  announceOutput: boolean;
  onAnnounce: (next: boolean) => void;
  t: T;
}) {
  return (
    <Popover
      trigger={
        <Button size="sm" variant="ghost">
          {t("terminal.actions.display")}
        </Button>
      }
      title={t("terminal.actions.display")}
      side="bottom"
      align="end"
    >
      <div className="flex flex-col gap-3">
        <Checkbox label={t("terminal.actions.textView")} description={t("terminal.actions.textViewHelp")} checked={textView} onCheckedChange={onTextView} />
        <Checkbox label={t("terminal.actions.announce")} description={t("terminal.actions.announceHelp")} checked={announceOutput} onCheckedChange={onAnnounce} />
      </div>
    </Popover>
  );
}

/**
 * A terminal on one target: before it opens, while it runs, and after it ends.
 *
 * What the server allows is read first (`GET /api/terminal/status`): a switch that is off is
 * said with the command that turns it on, instead of a button that would only fail. Everything
 * about the session itself is `useTerminalSession`'s; the screen is `TerminalScreen`.
 */
export function TerminalPanel({ target, injected }: TerminalPanelProps) {
  const t = useT();
  const status = useQuery(terminalStatusQuery());
  const permitted = useHasPermission(target.kind === "app" ? "apps.terminal" : "server.terminal");
  const session = useTerminalSession(target, injected);
  const { state, source } = session;
  const [last, setLast] = useState<{ shell?: string; service?: string }>({});
  const [textView, setTextView] = useState(false);
  const [announceOutput, setAnnounceOutput] = useState(false);

  const ended = state.phase === "ended";
  const sentence = ended && state.ended !== null ? endedSentence(t, state.ended, state.info?.idleSeconds ?? 0) : null;
  useEffect(() => {
    if (sentence !== null) announce(sentence, "polite");
  }, [sentence]);

  const render = (settings: NonNullable<typeof status.data>): ReactNode => {
    const open = (options: { shell?: string; service?: string }): void => {
      setLast(options);
      session.open(options);
    };

    if (state.phase === "idle") {
      if (target.kind === "server" && !settings.server.allowed) return <Off target={target} status={settings} t={t} />;
      if (target.kind === "app" && !settings.enabled) return <Off target={target} status={settings} t={t} />;
      return <Start target={target} status={settings} permitted={permitted} loading={false} onOpen={open} t={t} />;
    }

    if (state.phase === "failed" && state.failure !== null) {
      return (
        <div className="flex flex-col gap-3">
          <ErrorBlock live error={state.failure.error} title={t("terminal.failed.title")} />
          <div>
            <Button
              variant="primary"
              icon={<SquareTerminal aria-hidden="true" />}
              onClick={() => {
                session.restart();
              }}
            >
              {t("terminal.actions.open")}
            </Button>
          </div>
        </div>
      );
    }

    const label = target.kind === "app" ? t("terminal.document.app", { domain: target.domain }) : t("terminal.document.server");
    const live = state.phase === "open" || state.phase === "reconnecting" || state.phase === "opening";

    return (
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-6 gap-y-2">
          <SessionHeader state={state} t={t} />
          <div className="flex flex-wrap items-center gap-2">
            <DisplayOptions textView={textView} onTextView={setTextView} announceOutput={announceOutput} onAnnounce={setAnnounceOutput} t={t} />
            {live ? (
              <Button icon={<LogOut aria-hidden="true" />} onClick={session.end}>
                {t("terminal.actions.end")}
              </Button>
            ) : (
              <Button
                variant={target.kind === "app" ? "secondary" : "primary"}
                icon={<Plus aria-hidden="true" />}
                onClick={() => {
                  // A new terminal is the same one again: the options it was opened with are kept.
                  session.restart();
                  open(last);
                }}
              >
                {t("terminal.actions.newSession")}
              </Button>
            )}
          </div>
        </div>
        <TerminalScreen
          source={source}
          label={label}
          className="h-editor"
          disabled={state.phase !== "open"}
          textView={textView}
          announce={announceOutput}
          focusOnMount
          onInput={session.send}
          onResize={session.resize}
          onCopied={(_text, ok) => {
            if (ok) toast.success(t("common.copyButton.copied"));
            else toast.error(t("common.copyButton.failed"));
          }}
        />
        <Notices state={state} t={t} />
        {sentence !== null ? <InlineMessage tone="info">{sentence}</InlineMessage> : null}
      </div>
    );
  };

  // A refresh of the settings that fails while a terminal is open says nothing: QueryState would
  // put its note above the screen and remount it, and the session does not depend on the answer.
  const query = { ...status, isError: status.data === undefined ? status.isError : false };
  return (
    <QueryState query={query} label={t("terminal.page.what")} skeleton={<Skeleton className="h-40 w-full rounded-card" />}>
      {render}
    </QueryState>
  );
}
