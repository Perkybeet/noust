import { Fragment } from "react";
import type { ReactNode } from "react";

import type { components } from "../../api/schema.gen";
import { useT } from "../../i18n";
import { DiffLines } from "./DiffLines";
import { Disclosure } from "./Disclosure";
import { ICONS } from "./icons";
import { InlineMessage } from "./InlineMessage";
import { Mono } from "./Mono";
import { Notice } from "./Notice";
import { ServerText } from "./ServerText";

/** A sentence the server says as a code, its English text and the fix when it has one. */
export interface PlanSentence {
  code: string;
  params?: Readonly<Record<string, string | number | boolean>>;
  text: string;
  hint?: string | null;
}

export interface PlanCommand {
  argv: readonly string[];
  text: string;
  probe: boolean;
  /** Not a process: a health check Noust makes itself. */
  check?: boolean;
  cwd?: string | null;
  runs_as?: string | null;
  seconds?: number | null;
  step?: string | null;
  writes_to?: string | null;
}

export interface PlanFile {
  path: string;
  change: string;
  shown: string;
  lines?: readonly { kind: string; text: string }[];
  truncated?: boolean;
  reason?: string | null;
  destination?: string | null;
}

export interface PlanStep {
  key: string;
  title: string;
  commands?: readonly number[];
}

/**
 * What Noust's own simulation says an operation would do: the shape every operation with a plan answers with
 * (`PlanOut`). The API leaves a field with a default out of its description's required ones; `planFrom` fills them in, so a
 * plan that arrives without a list has an empty one.
 */
export interface Plan {
  operation: string;
  subject?: string | null;
  effect: PlanSentence;
  commands: readonly PlanCommand[];
  files: readonly PlanFile[];
  steps: readonly PlanStep[];
  findings: readonly PlanSentence[];
  unknown: readonly PlanSentence[];
  needs: { elevation: boolean; approval: boolean; confirmations: readonly { field: string; kind: string; sentence: PlanSentence }[] };
  blocked: PlanSentence | null;
  asked_remote: boolean;
  omitted: number;
}

/**
 * The plan as the API answers it, with everything it may leave out put in.
 *
 * @param model - What the plan route (or a proposal) carried.
 * @returns The plan the preview draws.
 */
export function planFrom(model: components["schemas"]["PlanOutModel"]): Plan {
  return {
    operation: model.operation,
    subject: model.subject ?? null,
    effect: model.effect,
    commands: model.commands ?? [],
    files: model.files ?? [],
    steps: model.steps ?? [],
    findings: model.findings ?? [],
    unknown: model.unknown ?? [],
    needs: { elevation: model.needs?.elevation ?? false, approval: model.needs?.approval ?? false, confirmations: model.needs?.confirmations ?? [] },
    blocked: model.blocked ?? null,
    asked_remote: model.asked_remote,
    omitted: model.omitted,
  };
}

export interface PlanPreviewProps {
  plan: Plan;
  /** Opened from the start, to be looked at (the proposal that is being decided). */
  defaultOpen?: boolean;
  /** A line under the title that says whose simulation it is, when the default ("Noust works this out…") is not what the place needs. */
  from?: ReactNode;
}

function Sentence({ sentence, part = "text" }: { sentence: PlanSentence; part?: "text" | "hint" }) {
  return <ServerText coded={sentence} text={part === "hint" ? sentence.hint : sentence.text} part={part} />;
}

function Heading({ children }: { children: ReactNode }) {
  return <h4 className="title text-13 text-fg">{children}</h4>;
}

function CommandLine({ command, number }: { command: PlanCommand; number: number }) {
  const t = useT();
  const notes = [
    command.check ? t("plans.preview.check") : command.probe ? t("plans.preview.probe") : null,
    command.runs_as !== null && command.runs_as !== undefined ? t("plans.preview.runsAs", { account: command.runs_as }) : null,
    command.writes_to !== null && command.writes_to !== undefined ? t("plans.preview.writesTo", { path: command.writes_to }) : null,
    command.seconds !== null && command.seconds !== undefined ? t("plans.preview.seconds", { seconds: command.seconds }) : null,
  ].filter((note): note is string => note !== null);
  return (
    <li className="flex min-w-0 gap-3">
      <span aria-hidden="true" className="w-4 shrink-0 text-right text-fg-muted tabular-nums">
        {number}
      </span>
      <span className="flex min-w-0 flex-col">
        {/* The order as the system would be asked, never reworded: it is what is typed or what is sent. */}
        <Mono className="min-w-0 break-words whitespace-pre-wrap">{command.text}</Mono>
        {notes.length > 0 ? <span className="font-sans text-12 text-fg-muted">{notes.join(". ")}</span> : null}
      </span>
    </li>
  );
}

function CommandsList({ commands, offset = 0, label }: { commands: readonly PlanCommand[]; offset?: number; label: string }) {
  return (
    <ol aria-label={label} className="elev-carved-fine m-0 flex list-none flex-col gap-1.5 rounded-control bg-bg-sunken px-3 py-2 text-12 leading-5">
      {commands.map((command, index) => (
        <CommandLine key={`${String(offset + index)}:${command.text}`} command={command} number={offset + index + 1} />
      ))}
    </ol>
  );
}

/** The orders, in order; grouped under the phases the operation announces when it announces any. */
function Commands({ plan }: { plan: Plan }) {
  const t = useT();
  if (plan.commands.length === 0) return null;
  const steps = plan.steps.filter((step) => (step.commands ?? []).length > 0);
  return (
    <section className="flex flex-col gap-1.5">
      <Heading>{t("plans.preview.commands")}</Heading>
      {steps.length === 0 ? (
        <CommandsList commands={plan.commands} label={t("plans.preview.commandsList")} />
      ) : (
        <div className="flex flex-col gap-3">
          {steps.map((step) => {
            const picked = (step.commands ?? []).flatMap((index) => {
              const command = plan.commands[index];
              return command === undefined ? [] : [{ command, index }];
            });
            return (
              <div key={step.key} className="flex flex-col gap-1">
                {/* The line the operation itself announces while it runs, in its own words. */}
                <p translate="no" className="text-12 font-medium text-fg-muted">
                  {step.title}
                </p>
                <ol aria-label={step.title} className="elev-carved-fine m-0 flex list-none flex-col gap-1.5 rounded-control bg-bg-sunken px-3 py-2 text-12 leading-5">
                  {picked.map(({ command, index }) => (
                    <CommandLine key={`${String(index)}:${command.text}`} command={command} number={index + 1} />
                  ))}
                </ol>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function FileBlock({ file }: { file: PlanFile }) {
  const t = useT();
  const change = file.change in CHANGE_WORDS ? t(CHANGE_WORDS[file.change as keyof typeof CHANGE_WORDS]) : file.change;
  const reason = file.reason !== null && file.reason !== undefined && file.reason in REASON_WORDS ? REASON_WORDS[file.reason as keyof typeof REASON_WORDS] : "plans.preview.notShown.none";
  return (
    <li className="flex min-w-0 flex-col gap-1.5">
      <p className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 text-12 text-fg-muted">
        <span>{change}</span>
        <Mono tone="default" className="break-all">
          {file.path}
        </Mono>
        {file.destination !== null && file.destination !== undefined ? (
          <span>
            {t.rich("plans.preview.destination", { path: <Mono tone="default" className="break-all">{file.destination}</Mono> })}
          </span>
        ) : null}
      </p>
      {file.shown === "none" ? (
        file.reason !== null && file.reason !== undefined ? (
          <p className="text-12 text-fg-muted">{t(reason)}</p>
        ) : null
      ) : (
        <>
          <DiffLines lines={(file.lines ?? []).map((line) => ({ kind: line.kind === "add" || line.kind === "del" ? line.kind : "same", text: line.text }))} label={t("plans.preview.diffOf", { path: file.path })} />
          {file.shown === "names" ? <p className="text-12 text-fg-muted">{t("plans.preview.namesOnly")}</p> : null}
          {file.truncated === true ? <p className="text-12 text-fg-muted">{t("plans.preview.truncated")}</p> : null}
        </>
      )}
    </li>
  );
}

const CHANGE_WORDS = {
  create: "plans.preview.fileChange.create",
  modify: "plans.preview.fileChange.modify",
  delete: "plans.preview.fileChange.delete",
  move: "plans.preview.fileChange.move",
  copy: "plans.preview.fileChange.copy",
  link: "plans.preview.fileChange.link",
  mkdir: "plans.preview.fileChange.mkdir",
  meta: "plans.preview.fileChange.meta",
} as const;

const REASON_WORDS = {
  secret: "plans.preview.notShown.secret",
  binary: "plans.preview.notShown.binary",
  large: "plans.preview.notShown.large",
  unreadable: "plans.preview.notShown.unreadable",
} as const;

function Files({ plan }: { plan: Plan }) {
  const t = useT();
  if (plan.files.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5">
      <Heading>{t("plans.preview.files")}</Heading>
      <ul aria-label={t("plans.preview.filesList")} className="m-0 flex list-none flex-col gap-3 p-0">
        {plan.files.map((file) => (
          <FileBlock key={`${file.change}:${file.path}`} file={file} />
        ))}
      </ul>
    </section>
  );
}

function Needs({ plan }: { plan: Plan }) {
  const t = useT();
  const { elevation, approval, confirmations } = plan.needs;
  if (!elevation && !approval && confirmations.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5">
      <Heading>{t("plans.preview.needs")}</Heading>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-13 text-fg">
        {elevation ? (
          <li className="flex items-start gap-2">
            <ICONS.elevated aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0 text-fg-muted" />
            {t("plans.preview.needsElevation")}
          </li>
        ) : null}
        {approval ? (
          <li className="flex items-start gap-2">
            <ICONS.info aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0 text-fg-muted" />
            {t("plans.preview.needsApproval")}
          </li>
        ) : null}
        {confirmations.map((confirmation) => (
          <li key={confirmation.field} className="flex items-start gap-2">
            <ICONS.info aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0 text-fg-muted" />
            <span className="min-w-0 text-pretty">
              {t("plans.preview.needsConfirmation")} <Sentence sentence={confirmation.sentence} />
              {confirmation.sentence.hint !== null && confirmation.sentence.hint !== undefined ? (
                <>
                  {" "}
                  <span className="text-fg-muted">
                    <Sentence sentence={confirmation.sentence} part="hint" />
                  </span>
                </>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The figures of the fold's summary, said as the language lists things (never glued by hand). */
function summaryOf(plan: Plan, t: ReturnType<typeof useT>): string {
  const parts = [t("plans.preview.commandsCount", { count: plan.commands.length })];
  if (plan.files.length > 0) parts.push(t("plans.preview.filesCount", { count: plan.files.length }));
  if (plan.unknown.length > 0) parts.push(t("plans.preview.unknownCount", { count: plan.unknown.length }));
  return new Intl.ListFormat(t.locale, { style: "short", type: "unit" }).format(parts);
}

/**
 * "See what it will do" (item 183): the orders in the order they would run, the files they would change and, never
 * folded away, what the simulation does not know. It is Noust's own simulation and never the assistant's words, so a
 * person's dialog (updating an application, cleaning up Docker) and a proposal draw the same thing. An operation with
 * no simulation does not draw this at all: it is not offered, instead of shown empty. A plan that would be refused now
 * is still a plan: it says why and still shows what it would have done.
 */
export function PlanPreview({ plan, defaultOpen = false, from }: PlanPreviewProps) {
  const t = useT();
  return (
    <Disclosure variant="card" label={t("plans.preview.title")} summary={summaryOf(plan, t)} {...(defaultOpen ? { defaultOpen: true } : {})}>
      <div className="flex flex-col gap-4 pt-1" data-plan={plan.operation}>
        <p className="text-12 text-pretty text-fg-muted">{from ?? t("plans.preview.from")}</p>
        {plan.blocked !== null ? (
          <Notice tone="warning" title={t("plans.preview.blocked")}>
            <Sentence sentence={plan.blocked} />
          </Notice>
        ) : null}
        <section className="flex flex-col gap-1">
          <Heading>{t("plans.preview.effect")}</Heading>
          <p className="text-13 text-pretty text-fg">
            <Sentence sentence={plan.effect} />
          </p>
        </section>
        <Commands plan={plan} />
        <Files plan={plan} />
        {plan.findings.length > 0 ? (
          <section className="flex flex-col gap-1">
            <Heading>{t("plans.preview.found")}</Heading>
            <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-13 text-fg">
              {plan.findings.map((finding, index) => (
                <li key={`${finding.code}:${String(index)}`} className="text-pretty">
                  <Sentence sentence={finding} />
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        {plan.unknown.length > 0 ? (
          <section className="flex flex-col gap-1.5" aria-label={t("plans.preview.unknown")}>
            <Heading>{t("plans.preview.unknown")}</Heading>
            <p className="text-12 text-pretty text-fg-muted">{t("plans.preview.unknownHelp")}</p>
            <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
              {plan.unknown.map((item, index) => (
                <Fragment key={`${item.code}:${String(index)}`}>
                  <li>
                    <InlineMessage tone="info">
                      <Sentence sentence={item} />
                    </InlineMessage>
                  </li>
                </Fragment>
              ))}
            </ul>
          </section>
        ) : null}
        <Needs plan={plan} />
        {plan.asked_remote ? <p className="text-12 text-fg-muted">{t("plans.preview.askedRemote")}</p> : null}
        {plan.omitted > 0 ? <p className="text-12 text-fg-muted">{t("plans.preview.omitted", { count: plan.omitted })}</p> : null}
      </div>
    </Disclosure>
  );
}
