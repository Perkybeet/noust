import { AiMark, locationWord } from "../../components/assistant/AiMark";
import { LiteralLines, MessageText } from "../../components/assistant/MessageText";
import { NotOffered as NotOfferedLine, ProposalCard } from "../../components/assistant/ProposalCard";
import type { ProposalView } from "../../components/assistant/ProposalCard";
import { ReadSteps } from "../../components/assistant/ReadSteps";
import type { ReadStep } from "../../components/assistant/ReadSteps";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Mono } from "../../components/ui/Mono";
import { ServerText, useServerSentence } from "../../components/ui/ServerText";
import { ErrorBlock } from "../../components/page/QueryState";
import { TextLink } from "../../components/ui/TextLink";
import { useCan } from "../../components/page/permissions";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import type { LiveTurn, Proposal, Sentence, Step, Turn } from "./model";

/** A sentence of Noust's, said in the console's language. */
function Said({ sentence, part = "text" }: { sentence: Sentence; part?: "text" | "hint" }) {
  return <ServerText coded={sentence} text={part === "hint" ? sentence.hint : sentence.text} part={part} />;
}

function sizeWords(t: T, step: Step): string | undefined {
  if (step.size === null) return undefined;
  const unit = step.size.unit === "lines" || step.size.unit === "items" || step.size.unit === "chars" || step.size.unit === "results" ? step.size.unit : null;
  return unit === null ? undefined : t(`assistant.steps.size.${unit}`, { count: step.size.count });
}

/** What Noust wrote of each step, with how much it read; the server it ran on is said only on a fleet. */
export function readSteps(t: T, steps: readonly Step[], fleet: boolean): ReadStep[] {
  return steps.map((step) => {
    const size = sizeWords(t, step);
    return {
      id: step.id,
      what: step.what !== null ? <Said sentence={step.what} /> : <Mono>{step.tool}</Mono>,
      ...(size !== undefined ? { size } : {}),
      masked: step.masked,
      outside: step.outside,
      state: step.state,
      ...(step.error !== null ? { error: step.error } : {}),
      ...(fleet && step.server !== "" ? { server: step.server } : {}),
    };
  });
}

/** The line of a proposal the console wrote from the call, in the person's words. */
function view(t: T, proposal: Proposal): ProposalView {
  const labels: Record<string, string> = { domain: t("assistant.proposal.facts.application"), server: t("assistant.proposal.facts.server"), environment: t("assistant.proposal.facts.environment") };
  return {
    id: proposal.id,
    title: proposal.title !== null ? <Said sentence={proposal.title} /> : proposal.effect,
    klass: proposal.klass,
    state: proposal.state,
    permission: proposal.permission,
    call: `${proposal.call.method} ${proposal.call.path}`,
    facts: proposal.facts.map((fact) => ({
      label: labels[fact.name] ?? fact.name,
      value: fact.value,
      ...(fact.foreign !== null
        ? {
            foreign: {
              kind: fact.foreign.kind,
              where:
                fact.foreign.source === null ? (
                  t("assistant.proposal.foreign.nowhere")
                ) : fact.foreign.label !== null && fact.foreign.line !== null ? (
                  t.rich("assistant.proposal.foreign.foundIn", { source: <Said sentence={fact.foreign.label} />, line: fact.foreign.line })
                ) : fact.foreign.label !== null ? (
                  t.rich("assistant.proposal.foreign.foundInSource", { source: <Said sentence={fact.foreign.label} /> })
                ) : (
                  t("assistant.proposal.foreign.nowhere")
                ),
            },
          }
        : {}),
    })),
    plan: proposal.plan,
    why: proposal.why !== null ? <Said sentence={proposal.why} /> : null,
    ...(proposal.decidedBy !== null ? { decidedBy: proposal.decidedBy } : {}),
    ...(proposal.decidedAt !== null ? { decidedAt: proposal.decidedAt.slice(11, 16) } : {}),
    ...(proposal.result !== null ? { result: typeof proposal.result === "string" ? proposal.result : <Said sentence={proposal.result} /> } : {}),
    ...(proposal.output !== null ? { output: proposal.output } : {}),
    approval: proposal.approval,
    verified: proposal.verified,
  };
}

export interface TurnViewProps {
  turn: Turn;
  fleet: boolean;
  busy: string | null;
  onApprove: (proposal: Proposal) => void;
  onReject: (proposal: Proposal) => void;
  onAskAgain: (question: string) => void;
}

/** One question and what came of it: the answer with its mark, what it read, what it proposes, what is not offered and where it stopped. */
export function TurnView({ turn, fleet, busy, onApprove, onReject, onAskAgain }: TurnViewProps) {
  const t = useT();
  const say = useServerSentence();
  const canPolicy = useCan("settings.read");
  const canAudit = useCan("audit.read");
  const waiting = turn.proposals.findIndex((proposal) => proposal.state === "waiting");
  const links = {
    ...(canPolicy ? { policy: <TextLink to="/settings/approval-policy" size="ui">{t("assistant.proposal.seePolicy")}</TextLink> } : {}),
    ...(canAudit ? { audit: <TextLink to="/settings/audit" size="inline">{t("assistant.proposal.seeAudit")}</TextLink>, approval: <TextLink to="/settings/approvals" size="inline">{t("assistant.proposal.seeApproval")}</TextLink> } : {}),
  };
  return (
    <div className="flex flex-col gap-3" data-turn={turn.id}>
      <div className="flex flex-col gap-1">
        <p className="text-12 text-fg-muted">{t("assistant.panel.you")}</p>
        <p className="rounded-control bg-bg-sunken px-3 py-2 text-13 text-pretty text-fg">{turn.question}</p>
      </div>
      <AiMark model={turn.model} where={locationWord(t, turn.location)} at={turn.endedAt !== null ? <RelativeTime value={turn.endedAt} /> : undefined} />
      {turn.parts.map((part, index) =>
        part.type === "literal" ? (
          <LiteralLines key={`${part.source.id}:${String(index)}`} caption={part.source.label !== null ? <Said sentence={part.source.label} /> : t("assistant.message.literal")} lines={part.lines} />
        ) : (
          <MessageText key={`text${String(index)}`} text={part.text} />
        ),
      )}
      {turn.parts.length === 0 && turn.text !== "" ? <MessageText text={turn.text} /> : null}
      <ReadSteps steps={readSteps(t, turn.steps, fleet)} />
      {turn.warnings.map((warning) => (
        <InlineMessage key={warning.code} tone="warning">
          <Said sentence={warning} />
        </InlineMessage>
      ))}
      {turn.proposals.length > 1 ? <p className="text-12 text-fg-muted">{t("assistant.proposal.count", { count: turn.proposals.length })}</p> : null}
      {turn.proposals.map((proposal, index) => (
        <ProposalCard
          key={proposal.id}
          proposal={view(t, proposal)}
          primary={index === waiting}
          links={links}
          busy={busy === proposal.id}
          onApprove={() => onApprove(proposal)}
          onReject={() => onReject(proposal)}
        />
      ))}
      {turn.notOffered.map((item, index) => (
        <NotOfferedLine
          key={`${item.floor ? "floor" : "policy"}:${String(index)}`}
          title={item.title !== null ? <Said sentence={item.title} /> : ""}
          why={item.why !== null ? <Said sentence={item.why} /> : ""}
          {...(item.why?.hint !== null && item.why?.hint !== undefined ? { hint: <Said sentence={item.why} part="hint" /> } : {})}
          floor={item.floor}
          {...(canPolicy && !item.floor ? { policyLink: <TextLink to="/settings/approval-policy" size="inline">{t("assistant.proposal.notOffered.seePolicy")}</TextLink> } : {})}
        />
      ))}
      {turn.stop !== null ? (
        <InlineMessage tone="warning">
          <Said sentence={turn.stop} /> {turn.stop.hint !== null ? <Said sentence={turn.stop} part="hint" /> : null}
        </InlineMessage>
      ) : null}
      {turn.state === "cancelled" ? <p className="text-12 text-fg-muted">{t("assistant.panel.cancelled")}</p> : null}
      {turn.error !== null ? (
        <ErrorBlock
          compact
          live
          title={t("assistant.panel.couldNotAnswer")}
          error={{ detail: say(turn.error, turn.error.text), ...(turn.error.output !== null && turn.error.output !== "" ? { output: turn.error.output } : {}) }}
          {...(turn.error.hint !== null ? { hint: say(turn.error, turn.error.hint, "hint") } : {})}
          action={
            <Button size="sm" variant="secondary" onClick={() => onAskAgain(turn.question)}>
              {t("assistant.panel.askAgain")}
            </Button>
          }
        />
      ) : null}
    </div>
  );
}

export interface LiveViewProps {
  live: LiveTurn;
  question: string;
  fleet: boolean;
  onStop: () => void;
}

/** A turn that is being written: the question, what it is reading now and the draft of its answer, which announces a sentence at a time and never a word. */
export function LiveView({ live, question, fleet, onStop }: LiveViewProps) {
  const t = useT();
  return (
    <div className="flex flex-col gap-3" data-turn={live.id} data-live="">
      <div className="flex flex-col gap-1">
        <p className="text-12 text-fg-muted">{t("assistant.panel.you")}</p>
        <p className="rounded-control bg-bg-sunken px-3 py-2 text-13 text-pretty text-fg">{question}</p>
      </div>
      <AiMark model={live.model} where={locationWord(t, live.location)} />
      <ReadSteps running steps={readSteps(t, live.steps, fleet)} />
      {/* The words arrive in small pieces: this region is silent, and the announcer says each step and the end instead. */}
      <div aria-live="off">
        {live.draft !== "" ? <MessageText text={live.draft} /> : null}
      </div>
      <div>
        <Button size="sm" variant="secondary" onClick={onStop}>
          {t("common.words.stop")}
        </Button>
      </div>
    </div>
  );
}
