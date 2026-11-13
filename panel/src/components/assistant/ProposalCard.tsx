import { Check } from "lucide-react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { KeyValueList } from "../page/KeyValueList";
import { HelpPopover } from "../ui/HelpPopover";
import { ICONS } from "../ui/icons";
import { Mono } from "../ui/Mono";
import { Notice } from "../ui/Notice";
import { OBJECT_ICONS, POLICY_ICONS } from "../ui/objectIcons";
import type { Plan } from "../ui/PlanPreview";
import { PlanPreview } from "../ui/PlanPreview";
import { Spinner } from "../ui/Spinner";
import { StatusGlyph } from "../ui/StatusPill";
import { SystemOutput } from "../ui/SystemOutput";

/**
 * How much of the decision is the person's. `alone`: the policy lets the assistant do it and it is already done (a record, with no
 * buttons). `ask`: it waits for the person's approval, once. `you`: the person also confirms it is them (sudo mode). `two`: it
 * also waits for a second person, which the person never satisfies alone.
 */
export type ProposalClass = "alone" | "ask" | "you" | "two";
export type ProposalState = "waiting" | "running" | "second" | "done" | "failed" | "rejected";

export interface ProposalFact {
  /** What the value is ("Application", "Server"). */
  label: string;
  value: string;
  /** A value that is not the person's or Noust's, and where it was found; the card highlights it and forces the question. */
  foreign?: { kind: string; where: ReactNode };
}

export interface ProposalView {
  id: string;
  /** What it does in one line, as a sentence Noust wrote from the call: never the model's prose. */
  title: ReactNode;
  klass: ProposalClass;
  state: ProposalState;
  /** The permission the call needs, in mono, so the class has its reason. */
  permission: string;
  /** The exact call as the server will see it: `POST /api/apps/shop.example.com/restart`. */
  call: string;
  facts: readonly ProposalFact[];
  /** Noust's simulation of the call, where the operation has one. */
  plan: Plan | null;
  /** Why it is this class, in the sentence Noust wrote. */
  why: ReactNode | null;
  decidedBy?: string;
  decidedAt?: string;
  /** What came out when it is done or failed, in a sentence. */
  result?: ReactNode;
  /** The system's words when it failed, verbatim. */
  output?: string;
  /** The request a second person decides, when it waits for one. */
  approval?: number | null;
  /** Whether the audit trail proved the call was made; false means nobody could. */
  verified?: boolean;
}

const CLASS_ICON = { alone: POLICY_ICONS.alone, ask: POLICY_ICONS.propose, you: ICONS.elevated, two: OBJECT_ICONS.team } as const;

/** A value that is not the person's or Noust's: a host, an address, a command. Shape, ground and word, never the ground alone. */
export function ForeignValue({ children }: { children: string }) {
  const t = useT();
  return (
    <span className="inline-flex max-w-full items-start gap-1.5 rounded-chip border border-warn-border bg-warn-soft px-1.5 py-0.5">
      <StatusGlyph state="warning" size={12} className="mt-1 shrink-0 text-warn" />
      <Mono className="break-all">{children}</Mono>
      <span className="sr-only">{t("assistant.proposal.foreign.mark")}</span>
    </span>
  );
}

function classWord(t: T, klass: ProposalClass): string {
  return t(`assistant.proposal.class.${klass}`);
}

function ClassLine({ proposal, policyLink }: { proposal: ProposalView; policyLink?: ReactNode }) {
  const t = useT();
  const Icon = CLASS_ICON[proposal.klass];
  return (
    <div className="flex items-start gap-2 text-13">
      <Icon aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-start justify-between gap-1.5 font-medium text-fg">
          <span className="min-w-0 text-pretty">{classWord(t, proposal.klass)}</span>
          <HelpPopover label={t("assistant.proposal.why")} title={classWord(t, proposal.klass)}>
            <div className="flex flex-col gap-2 text-13 text-pretty text-fg-muted">
              <p>{t(`assistant.proposal.classHelp.${proposal.klass}`)}</p>
              <p>
                {t("assistant.proposal.exactCall")} <Mono className="break-words">{proposal.call}</Mono>
              </p>
              <p>
                {t("assistant.proposal.needsPermission")} <Mono>{proposal.permission}</Mono>.
              </p>
              {policyLink !== undefined && policyLink !== null && policyLink !== false ? policyLink : <p>{t("assistant.proposal.policyIsOthers")}</p>}
            </div>
          </HelpPopover>
        </div>
        {proposal.why !== null ? <p className="text-12 text-pretty text-fg-muted">{proposal.why}</p> : null}
      </div>
    </div>
  );
}

export interface ProposalCardProps {
  proposal: ProposalView;
  /** The first proposal that waits is the one primary action of the panel; the others stay secondary. */
  primary?: boolean;
  /**
   * The places a card leads to, built by whoever knows the routes (the kit does not): the policy that decided its class, the
   * audit trail that proves it was done and the approval a second person decides. Each is drawn only for who may read it.
   */
  links?: { policy?: ReactNode; audit?: ReactNode; approval?: ReactNode } | undefined;
  /** Decides it. Absent, the card is a record. */
  onApprove?: () => void;
  onReject?: () => void;
  busy?: boolean;
}

function Footer({ proposal, primary, links, onApprove, onReject, busy }: Omit<ProposalCardProps, "policyLink"> & { proposal: ProposalView }) {
  const t = useT();
  switch (proposal.state) {
    case "waiting":
      return (
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button size="sm" variant="secondary" onClick={onReject} disabled={busy === true}>
            {t("assistant.proposal.reject")}
          </Button>
          <Button size="sm" variant={primary === true ? "primary" : "secondary"} icon={proposal.klass === "you" ? <ICONS.elevated aria-hidden="true" /> : undefined} onClick={onApprove} loading={busy === true}>
            {proposal.klass === "you" ? t("assistant.proposal.approveElevated") : t("assistant.proposal.approve")}
          </Button>
        </div>
      );
    case "running":
      return (
        <p className="flex items-center gap-2 text-13 text-fg" role="status">
          <Spinner size={14} className="text-warn" />
          {t("assistant.proposal.doing")}
        </p>
      );
    case "done":
      return (
        <div className="flex flex-col gap-1">
          <p className="flex items-start gap-2 text-13 text-fg">
            <Check aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
            <span className="min-w-0 text-pretty">{proposal.result ?? t("assistant.proposal.done")}</span>
          </p>
          {proposal.decidedAt !== undefined ? (
            <p className="pl-6 text-12 text-fg-muted">
              {proposal.klass === "alone" ? t("assistant.proposal.decidedAlone", { at: proposal.decidedAt }) : t("assistant.proposal.decidedBy", { by: proposal.decidedBy ?? "", at: proposal.decidedAt })}{" "}
              {links?.audit}
            </p>
          ) : null}
          {proposal.verified === false ? <p className="pl-6 text-12 text-fg-muted">{t("assistant.proposal.unverified")}</p> : null}
        </div>
      );
    case "failed":
      return (
        <div className="flex flex-col gap-2">
          <p className="flex items-start gap-2 text-13 font-medium text-fg">
            <StatusGlyph state="failed" className="mt-1 text-fail" />
            <span className="min-w-0 text-pretty">{proposal.result ?? t("assistant.proposal.couldNot")}</span>
          </p>
          {proposal.output !== undefined && proposal.output !== "" ? (
            <SystemOutput label={t("assistant.proposal.systemSaid")} className="elev-carved-fine rounded-control bg-bg-sunken px-3 py-2">
              {proposal.output}
            </SystemOutput>
          ) : null}
        </div>
      );
    case "rejected":
      return (
        <p className="flex items-start gap-2 text-13 text-fg">
          <ICONS.close aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
          <span className="min-w-0 text-pretty">{t("assistant.proposal.rejectedBy", { by: proposal.decidedBy ?? "", at: proposal.decidedAt ?? "" })}</span>
        </p>
      );
    case "second":
      return (
        <p className="flex items-start gap-2 text-13 text-fg" role="status">
          <StatusGlyph state="queued" className="mt-1 text-warn" />
          <span className="min-w-0 text-pretty">
            {proposal.approval !== null && proposal.approval !== undefined ? t("assistant.proposal.waitsForRequest", { request: proposal.approval }) : t("assistant.proposal.waitsFor")}{" "}
            {links?.approval}
          </span>
        </p>
      );
  }
}

/**
 * A proposal, drawn by the console from the exact call and never from the model's words: what it will do in one line, on what,
 * which of four classes it is, "See what it will do" where Noust can simulate it, and the two buttons that decide it. The class
 * says how much of the decision is the person's. A value that did not come from the person or from Noust is highlighted with
 * shape, ground and word, and forces the question. One primary at most (the first that waits) and never an "approve all".
 */
export function ProposalCard({ proposal, primary = false, links, onApprove, onReject, busy = false }: ProposalCardProps) {
  const t = useT();
  const foreign = proposal.facts.find((fact) => fact.foreign !== undefined);
  return (
    <Card as="article" padding="sm" level={4} className={cx(proposal.state === "rejected" && "bg-bg-sunken")} title={proposal.title}>
      <div className="flex flex-col gap-3 pt-1" data-proposal={proposal.id} data-class={proposal.klass} data-state={proposal.state}>
        <KeyValueList
          items={proposal.facts.map((fact) => ({
            label: fact.label,
            copy: false as const,
            mono: false,
            value:
              fact.foreign !== undefined ? (
                <span className="inline-flex min-w-0 flex-wrap items-center gap-x-1.5">
                  <ForeignValue>{fact.value}</ForeignValue>
                  <span className="text-12 text-fg-muted">{t(`assistant.proposal.foreign.kind.${foreignKind(fact.foreign.kind)}`)}</span>
                </span>
              ) : (
                <Mono>{fact.value}</Mono>
              ),
          }))}
        />
        {foreign?.foreign !== undefined ? (
          <Notice tone="warning" title={t("assistant.proposal.foreign.title")}>
            {foreign.foreign.where}
          </Notice>
        ) : null}
        <ClassLine proposal={proposal} policyLink={links?.policy} />
        {proposal.plan !== null ? <PlanPreview plan={proposal.plan} from={t("assistant.proposal.planFrom")} /> : null}
        <Footer proposal={proposal} primary={primary} links={links} {...(onApprove !== undefined ? { onApprove } : {})} {...(onReject !== undefined ? { onReject } : {})} busy={busy} />
      </div>
    </Card>
  );
}

const FOREIGN_KINDS = ["host", "address", "url", "email", "command", "value"] as const;

/** The kind a server names, or "value" for one this console does not know. */
function foreignKind(kind: string): (typeof FOREIGN_KINDS)[number] {
  return FOREIGN_KINDS.find((candidate) => candidate === kind) ?? "value";
}

export interface NotOfferedProps {
  /** What it would have been, as a sentence. */
  title: ReactNode;
  /** Why it is not offered, and what it would take (the sentence's hint says who changes it). */
  why: ReactNode;
  hint?: ReactNode;
  /** The floor: nobody changes it, so no link to a policy is drawn. */
  floor?: boolean;
  /** A link to the line of the policy that would change it, built by whoever knows the routes and may read it. */
  policyLink?: ReactNode;
}

/**
 * What is not offered: the policy does not let the assistant do it, or its floor never does. Not a card with buttons and not an
 * error: a line that says what it would have been, why it is not offered, what it would take and who changes that. Never a button
 * that does nothing.
 */
export function NotOffered({ title, why, hint, floor = false, policyLink }: NotOfferedProps) {
  const t = useT();
  return (
    <Card as="article" padding="sm" level={4} icon={ICONS.locked} title={t("assistant.proposal.notOffered.title")}>
      <div className="flex flex-col gap-2 pt-1 text-13 text-pretty text-fg" data-not-offered={floor ? "floor" : "policy"}>
        <p className="font-medium text-fg">{title}</p>
        <p className="text-fg-muted">{why}</p>
        {hint !== undefined ? <p>{hint}</p> : null}
        {floor ? <p className="text-fg-muted">{t("assistant.proposal.notOffered.floor")}</p> : policyLink !== undefined ? <p>{policyLink}</p> : null}
      </div>
    </Card>
  );
}
