import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronDown, CircleHelp, CircleMinus, CircleX, History, RotateCw, ScrollText } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { appQuery, diagnosisQuery } from "../../../api/queries/apps";
import type { Diagnosis } from "../../../api/queries/apps";
import { announce } from "../../../app/Announcer";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section, Sections } from "../../../components/page/Section";
import { JobProgress } from "../../../components/page/JobProgress";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ICONS } from "../../../components/ui/icons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { hasUnit } from "../../apps/AppRowActions";
import { useAppActions } from "../../apps/useAppActions";
import { RollbackDialog } from "../RollbackDialog";
import { checkLabel, checkStatus, headline, isProblem, openChecks, tally, verdictAnnouncement, verdictView } from "./view";
import type { Check, Tone } from "./view";

/** A verdict drawn in the state language: healthy is running, degraded a warning, down failed. */
const VERDICT_STATE: Record<Tone, Status> = { ok: "running", warn: "warning", fail: "failed", idle: "unknown" };

/** A check's outcome as a shape and a word, the colour of its outcome: never the colour alone. */
function CheckIcon({ tone, className }: { tone: Tone; className?: string }) {
  const Icon = tone === "ok" ? ICONS.success : tone === "warn" ? ICONS.warning : tone === "fail" ? CircleX : CircleMinus;
  const colour = tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : tone === "fail" ? "text-fail" : "text-idle";
  return <Icon aria-hidden="true" className={cx("shrink-0", colour, className)} />;
}

/** Where to go next from a check, when another tab shows more of the same. */
function nextStep(check: Check, domain: string, t: T): ReactNode {
  switch (check.name) {
    case "journal":
      return (
        <TextLink to="/apps/$domain/logs" params={{ domain }} size="ui">
          {t("appPages.diagnose.followLiveLog")}
        </TextLink>
      );
    case "last_deployment":
      return (
        <TextLink to="/apps/$domain/deployments" params={{ domain }} size="ui">
          {t("appPages.diagnose.openDeployments")}
        </TextLink>
      );
    case "certificate":
      return (
        <TextLink to="/apps/$domain/domains" params={{ domain }} size="ui">
          {t("appPages.diagnose.openDomains")}
        </TextLink>
      );
    default:
      return null;
  }
}

/**
 * What to do about a verdict that is not healthy, beside it: read the logs, go back to a version
 * that worked, or restart. Secondary: Update in the header stays the page's one primary action.
 */
function Remedies({ domain, t }: { domain: string; t: T }) {
  const app = useQuery(appQuery(domain));
  const { restart } = useAppActions(domain);
  const [rollingBack, setRollingBack] = useState(false);
  if (app.data === undefined) return null;
  const unit = hasUnit(app.data);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {unit ? (
        <Link to="/apps/$domain/logs" params={{ domain }} className={buttonClassName("secondary", "sm")}>
          <ScrollText aria-hidden="true" />
          {t("appPages.diagnose.viewLogs")}
        </Link>
      ) : null}
      <Button size="sm" icon={<History aria-hidden="true" />} onClick={() => setRollingBack(true)}>
        {t("appPages.diagnose.rollBack")}
      </Button>
      {unit ? (
        <Button size="sm" icon={<RotateCw aria-hidden="true" />} loading={restart.isPending} onClick={() => restart.mutate()}>
          {t("appPages.diagnose.restart")}
        </Button>
      ) : null}
      <RollbackDialog domain={domain} layout={app.data.layout} open={rollingBack} onOpenChange={setRollingBack} />
    </div>
  );
}

function Verdict({
  domain,
  diagnosis,
  checkedAt,
  running,
  onRunAgain,
  t,
}: {
  domain: string;
  diagnosis: Diagnosis;
  checkedAt: number;
  running: boolean;
  onRunAgain: () => void;
  t: T;
}) {
  const view = verdictView(diagnosis.verdict, t.locale);
  const counts = tally(diagnosis.checks);
  const healthy = view.tone === "ok";
  const parts = [
    { tone: "ok" as const, count: counts.ok, word: t("appPages.diagnose.tally.passed", { count: counts.ok }) },
    { tone: "warn" as const, count: counts.warn, word: t("appPages.diagnose.tally.warning", { count: counts.warn }) },
    { tone: "fail" as const, count: counts.fail, word: t("appPages.diagnose.tally.failed", { count: counts.fail }) },
    { tone: "idle" as const, count: counts.skip, word: t("appPages.diagnose.tally.skipped", { count: counts.skip }) },
  ].filter((part) => part.count > 0);

  return (
    <Card
      level={2}
      title={
        <span aria-busy={running || undefined} className="flex items-center gap-2">
          <span className="sr-only">{t("appPages.diagnose.verdictPrefix")}</span>{" "}
          <span data-verdict={diagnosis.verdict}>
            <StatusPill state={VERDICT_STATE[view.tone]} label={view.word} />
          </span>
        </span>
      }
      actions={
        <>
          <span className="hidden text-12 text-fg-muted sm:inline">{t.rich("appPages.diagnose.checkedAt", { time: <RelativeTime value={checkedAt} /> })}</span>
          <Button size="sm" icon={<RotateCw aria-hidden="true" />} loading={running} onClick={onRunAgain}>
            {t("appPages.diagnose.runAgain")}
          </Button>
        </>
      }
    >
      <div className="flex min-w-0 flex-col gap-4">
        <div className="flex max-w-measure flex-col gap-2">
          <div className="flex flex-col gap-0.5">
            <p className="text-12 text-fg-muted">{healthy ? t("appPages.diagnose.allGood") : t("appPages.diagnose.mostLikely")}</p>
            <p className="title text-18 text-pretty text-fg">{headline(diagnosis, t.locale)}</p>
          </div>
          {diagnosis.probable_cause ? (
            // The correlation's own words, verbatim, under the product's reading of them.
            <SystemOutput label={t("appPages.diagnose.whatTheChecksFound")} className="rounded-control border border-border bg-bg-sunken px-3 py-2">
              {diagnosis.probable_cause}
            </SystemOutput>
          ) : null}
        </div>

        {healthy ? null : <Remedies domain={domain} t={t} />}

        <p className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-13 text-fg-muted">
          <span>{t("appPages.diagnose.checksCount", { count: diagnosis.checks.length })}</span>
          {parts.map((part) => (
            <span key={part.tone} className="inline-flex items-center gap-1.5">
              <CheckIcon tone={part.tone} className="size-icon-sm" />
              <span>
                <span className="text-fg tabular-nums">{part.count}</span> {part.word}
              </span>
            </span>
          ))}
        </p>
      </div>
    </Card>
  );
}

/** The status column of a check row: shape and word, the word in the row's reading order. */
function CheckStatus({ status, t }: { status: string; t: T }) {
  const view = checkStatus(status, t.locale);
  return (
    <span className="inline-flex h-5 w-24 shrink-0 items-center gap-1.5 text-12 font-medium text-fg-muted">
      {view.tone === "idle" && status.trim().toLowerCase() !== "skip" ? (
        <CircleHelp aria-hidden="true" className="size-icon-md shrink-0 text-idle" />
      ) : (
        <CheckIcon tone={view.tone} className="size-icon-md" />
      )}
      <span className={view.tone === "fail" ? "text-fail" : undefined}>{view.word}</span>
    </span>
  );
}

const ROW = "flex min-w-0 flex-wrap items-start gap-x-3 gap-y-1 px-4 py-3";

function CheckRow({ check, domain, open, t }: { check: Check; domain: string; open: boolean; t: T }) {
  const evidence = check.evidence.trim() !== "";
  const next = nextStep(check, domain, t);
  const label = checkLabel(check.name, t.locale);
  const head = (
    <>
      <CheckStatus status={check.status} t={t} />
      <span className="w-44 shrink-0 text-13 leading-5 font-medium text-fg">{label}</span>
      <span className="min-w-0 flex-1 basis-60 text-13 leading-5 text-pretty text-fg-muted">{check.summary}</span>
    </>
  );

  if (!evidence) {
    return (
      <li className={ROW}>
        {head}
        <span className="ml-auto flex min-h-5 shrink-0 items-center gap-4 text-12 text-fg-muted">
          {next}
          <span>{t("appPages.diagnose.noOutput")}</span>
        </span>
      </li>
    );
  }

  return (
    <li>
      <details open={open} className="group">
        <summary className={cx(ROW, "cursor-pointer list-none -outline-offset-2 hover:bg-surface-hover [&::-webkit-details-marker]:hidden")}>
          {head}
          <span className="ml-auto flex min-h-5 shrink-0 items-center gap-1 text-12 text-fg-muted">
            <span className="group-open:hidden">{t("appPages.diagnose.showOutput")}</span>
            <span className="hidden group-open:inline">{t("appPages.diagnose.hideOutput")}</span>
            <ChevronDown aria-hidden="true" className="size-icon-sm transition-transform duration-(--duration-fast) group-open:rotate-180" />
          </span>
        </summary>
        <div className="flex flex-col gap-2 px-4 pb-4">
          <SystemOutput
            label={t("appPages.diagnose.outputOfCheck", { check: label.toLowerCase() })}
            maxHeight="max-h-72"
            className="rounded-control border border-border bg-bg-sunken px-3 py-2 leading-5"
          >
            {check.evidence}
          </SystemOutput>
          {next !== null ? <div>{next}</div> : null}
        </div>
      </details>
    </li>
  );
}

/**
 * Every probe, in the order it ran: what did not pass first and in full, the one the verdict
 * hangs on open with the logs it cites; what passed or was skipped folded into one line, one
 * click away.
 */
function Checks({ diagnosis, domain, t }: { diagnosis: Diagnosis; domain: string; t: T }) {
  const open = openChecks(diagnosis);
  // What did not pass, and what the verdict cites even when it passed (the logs of a failed
  // app), stay in view; the rest is folded.
  const shown = (check: Check): boolean => isProblem(check) || open.has(check.name);
  const problems = diagnosis.checks.filter(shown);
  const rest = diagnosis.checks.filter((check) => !shown(check));
  return (
    <Section title={t("appPages.diagnose.checksTitle")} description={t("appPages.diagnose.checksDescription")}>
      <Card padding="none">
        {/* Two lists side by side, never one inside the other: a check is one list item. */}
        <div className="flex min-w-0 flex-col divide-y divide-border">
          {problems.length > 0 ? (
            <ul aria-label={t("appPages.diagnose.problemsLabel")} className="flex min-w-0 flex-col divide-y divide-border">
              {problems.map((check) => (
                <CheckRow key={check.name} check={check} domain={domain} open={open.has(check.name)} t={t} />
              ))}
            </ul>
          ) : null}
          {rest.length > 0 ? (
            <details open={problems.length === 0} data-rest="" className="group/rest">
              <summary className={cx(ROW, "cursor-pointer list-none items-center -outline-offset-2 hover:bg-surface-hover [&::-webkit-details-marker]:hidden")}>
                <span className="text-13 font-medium text-fg">{t("appPages.diagnose.restSummary", { count: rest.length })}</span>
                <ChevronDown aria-hidden="true" className="ml-auto size-icon-sm text-fg-muted transition-transform duration-(--duration-fast) group-open/rest:rotate-180" />
              </summary>
              <ul aria-label={t("appPages.diagnose.restLabel")} className="flex min-w-0 flex-col divide-y divide-border border-t border-border">
                {rest.map((check) => (
                  <CheckRow key={check.name} check={check} domain={domain} open={open.has(check.name)} t={t} />
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      </Card>
    </Section>
  );
}

/**
 * While the probes run: what they are doing, as the job in hand, then the verdict's and the
 * checks' shape, so nothing moves when the answer arrives.
 */
function DiagnosisSkeleton({ t }: { t: T }) {
  return (
    <div className="flex flex-col gap-8">
      <JobProgress state="running" title={t("appPages.diagnose.running")} description={t("appPages.diagnose.runningChecks")} announce={false} />
      <div aria-hidden="true" className="flex flex-col gap-4">
        <Skeleton className="h-5 w-40" />
        <Card padding="none">
          <div className="flex flex-col divide-y divide-border">
            {Array.from({ length: 4 }, (_, i) => (
              <div key={i} className="flex h-11 items-center gap-3 px-4">
                <Skeleton className="h-3 w-16 sm:w-24" />
                <Skeleton className="h-3 w-28 sm:w-44" />
                <Skeleton className={cx("h-3", i % 2 === 0 ? "w-72" : "w-52")} />
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

/**
 * "Why is it down": every probe Noust can run about the app, correlated into a verdict. Noust's
 * reading first, in the product's words, with what to do about it; the correlation's own
 * sentence and each probe's output under it, verbatim. Nothing here changes the machine.
 */
export function DiagnoseTab({ domain }: { domain: string }) {
  const t = useT();
  useDocumentTitle(t("appPages.diagnose.documentTitle", { domain }), 1);
  // The probes take seconds and answer for now: never on focus, only when asked.
  const diagnosis = useQuery({ ...diagnosisQuery(domain), refetchOnWindowFocus: false });

  const runAgain = (): void => {
    void diagnosis.refetch().then((result) => {
      if (result.data === undefined || result.isError) return;
      announce(verdictAnnouncement(domain, result.data, t.locale), result.data.verdict === "down" ? "assertive" : "polite");
    });
  };

  return (
    <Sections>
      <section aria-label={t("appPages.diagnose.ariaLabel", { domain })} className="min-w-0">
        <QueryState query={diagnosis} label={t("appPages.diagnose.queryLabel", { domain })} skeleton={<DiagnosisSkeleton t={t} />}>
          {(data) => (
            <div className="flex flex-col gap-8">
              <Verdict domain={domain} diagnosis={data} checkedAt={diagnosis.dataUpdatedAt} running={diagnosis.isFetching} onRunAgain={runAgain} t={t} />
              <Checks diagnosis={data} domain={domain} t={t} />
            </div>
          )}
        </QueryState>
      </section>
      <CommandHint command={`noust diagnose ${domain}`} label={t("appPages.fromTerminal")} />
    </Sections>
  );
}
