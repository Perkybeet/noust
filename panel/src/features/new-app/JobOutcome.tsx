import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { CircleCheck, CircleMinus, ScrollText } from "lucide-react";
import { useEffect, useId } from "react";

import { importReportOf } from "../../api/queries/appImport";
import type { ImportReport, ImportStepReport } from "../../api/queries/appImport";
import { appKeys } from "../../api/queries/apps";
import { isJobFinished } from "../../api/queries/jobs";
import type { FollowedJob } from "../../api/queries/jobs";
import { recipeNotesOf } from "../../api/queries/recipes";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button, buttonClassName } from "../../components/ui/Button";
import { Spinner } from "../../components/ui/Spinner";
import { useT } from "../../i18n";
import { NoteText } from "./ExternalLink";
import { useDeploymentLanding } from "./useDeploymentLanding";
import type { LandingTarget } from "./useDeploymentLanding";

const LINK =
  "inline-flex w-fit items-center gap-1.5 rounded-[4px] text-13 font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus";

/** The deployment this job's build is recorded as, once the deployer wrote its row. */
function BuildLogLink({ domain, id }: { domain: string; id: number }) {
  const t = useT();
  return (
    <Link to="/apps/$domain/deployments/$id" params={{ domain, id: String(id) }} className={LINK}>
      <ScrollText aria-hidden="true" className="size-3.5" />
      {t("newApp.deploy.buildLog")}
    </Link>
  );
}

/** One part of an import: its state as a shape and a word, the server's name for it and why not, verbatim. */
function ReportRow({ step }: { step: ImportStepReport }) {
  const t = useT();
  return (
    <li className="flex items-start gap-2 text-13 text-fg">
      {step.applied ? (
        <CircleCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ok" />
      ) : (
        <CircleMinus aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
      )}
      <span className="flex min-w-0 flex-col gap-0.5">
        <span>
          <span className="sr-only">{`${t(step.applied ? "newApp.deploy.applied" : "newApp.deploy.notApplied")}: `}</span>
          <code translate="no" className="text-12">
            {step.part}
          </code>
        </span>
        {step.detail !== "" ? <span className="text-pretty text-fg-muted">{step.detail}</span> : null}
      </span>
    </li>
  );
}

/** What an import applied and what it did not, the parts that need a hand first. */
function Report({ report }: { report: ImportReport }) {
  const t = useT();
  const notAppliedId = useId();
  const appliedId = useId();
  const applied = report.steps.filter((step) => step.applied);
  return (
    <div className="flex flex-col gap-4">
      <p className="text-13 text-fg">{t("newApp.deploy.appliedCount", { count: report.steps.length, applied: String(applied.length) })}</p>
      {report.notApplied.length > 0 ? (
        <section aria-labelledby={notAppliedId} className="flex flex-col gap-2">
          <h3 id={notAppliedId} className="text-13 font-medium text-fg">
            {t("newApp.deploy.notApplied")}
          </h3>
          <p className="text-12 text-pretty text-fg-muted">{t("newApp.deploy.notAppliedHint")}</p>
          <ul className="flex flex-col gap-2">
            {report.notApplied.map((step) => (
              <ReportRow key={step.part} step={step} />
            ))}
          </ul>
        </section>
      ) : null}
      {applied.length > 0 ? (
        <section aria-labelledby={appliedId} className="flex flex-col gap-2">
          <h3 id={appliedId} className="text-13 font-medium text-fg">
            {t("newApp.deploy.applied")}
          </h3>
          <ul className="flex flex-col gap-2">
            {applied.map((step) => (
              <ReportRow key={step.part} step={step} />
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

/** A recipe's notes, once it is deployed: what to do next, with every address in them a link. */
function NextSteps({ notes }: { notes: readonly string[] }) {
  const t = useT();
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <h3 id={id} className="text-13 font-medium text-fg">
        {t("newApp.deploy.nextSteps")}
      </h3>
      <ol className="flex list-decimal flex-col gap-2 pl-5 text-13 text-pretty text-fg marker:text-fg-muted">
        {notes.map((note) => (
          <li key={note}>
            <NoteText note={note} />
          </li>
        ))}
      </ol>
    </section>
  );
}

export interface JobOutcomeProps {
  /** A recipe says what to do next; an import says what it applied. */
  kind: "recipe" | "import";
  target: LandingTarget;
  followedJob: FollowedJob;
  onBack: () => void;
}

/**
 * A recipe's deploy or an import, followed to its end on the wizard's own page: what comes
 * next is only known once the job has finished (the notes, the parts applied), so it does not
 * hand over to the deployment page as a plain deploy does. The build log is one link away as
 * soon as the deployer records it.
 */
export function JobOutcome({ kind, target, followedJob, onBack }: JobOutcomeProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const landing = useDeploymentLanding(target, followedJob);
  const job = followedJob.job;
  const finished = job !== null && isJobFinished(job);
  const log = landing?.kind === "deployment" ? <BuildLogLink domain={target.domain} id={landing.id} /> : null;

  useEffect(() => {
    // The application exists now, or failed to: the list and the palette read it again.
    if (finished) void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
  }, [finished, queryClient]);

  if (!finished) {
    const step = job?.current_step ?? null;
    return (
      <div role="status" className="flex min-w-0 flex-col gap-1.5 rounded-card border border-border bg-surface px-4 py-3 shadow-raised">
        <p className="flex items-center gap-2.5 text-14 font-medium text-fg">
          <Spinner size={16} className="text-warn" />
          {t(kind === "import" ? "newApp.deploy.importing" : "newApp.deploy.deploying", { domain: target.domain })}
        </p>
        <p className="flex min-w-0 flex-wrap items-center gap-x-2 text-13 text-fg-muted">
          <span>{t("newApp.deploy.stays")}</span>
          {step ? (
            <code translate="no" className="min-w-0 truncate text-12" title={step}>
              {step}
            </code>
          ) : null}
        </p>
        {log}
      </div>
    );
  }

  if (job.status !== "completed") {
    return (
      <div className="flex flex-col gap-3">
        <ErrorBlock
          live
          error={{ detail: job.error ?? t("newApp.deploy.failedNoDetail") }}
          title={t(kind === "import" ? "newApp.deploy.importFailed" : "newApp.deploy.failed", { domain: target.domain })}
          hint={t("newApp.deploy.failedAfterHint")}
        />
        {log}
        <div>
          <Button onClick={onBack}>{t("newApp.deploy.backToReview")}</Button>
        </div>
      </div>
    );
  }

  const notes = kind === "recipe" ? (recipeNotesOf(job.result) ?? []) : [];
  const report = kind === "import" ? importReportOf(job.result) : null;
  return (
    <div className="flex flex-col gap-5">
      <div role="status" className="flex min-w-0 flex-col gap-2 rounded-card border border-border bg-surface px-4 py-3 shadow-raised">
        <p className="flex items-center gap-2.5 text-14 font-medium text-fg">
          <CircleCheck aria-hidden="true" className="size-4 shrink-0 text-ok" />
          {t(kind === "import" ? "newApp.deploy.imported" : "newApp.deploy.deployed", { domain: target.domain })}
        </p>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Link to="/apps/$domain" params={{ domain: target.domain }} className={buttonClassName("primary", "md")}>
            {t("newApp.deploy.openApp", { domain: target.domain })}
          </Link>
          {log}
        </div>
      </div>
      {notes.length > 0 ? <NextSteps notes={notes} /> : null}
      {report !== null ? <Report report={report} /> : null}
    </div>
  );
}
