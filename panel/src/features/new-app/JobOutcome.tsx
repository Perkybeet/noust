import { useQueryClient } from "@tanstack/react-query";
import { CircleMinus, ScrollText } from "lucide-react";
import { useEffect, useId } from "react";

import { importReportOf } from "../../api/queries/appImport";
import type { ImportReport, ImportStepReport } from "../../api/queries/appImport";
import { appKeys } from "../../api/queries/apps";
import { isJobFinished } from "../../api/queries/jobs";
import type { FollowedJob } from "../../api/queries/jobs";
import { recipeNotesOf } from "../../api/queries/recipes";
import { JobProgress } from "../../components/page/JobProgress";
import { ICONS } from "../../components/ui/icons";
import { Mono } from "../../components/ui/Mono";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { NoteText } from "./NoteText";
import { useDeploymentLanding } from "./useDeploymentLanding";
import type { LandingTarget } from "./useDeploymentLanding";

/** The deployment this job's build is recorded as, once the deployer wrote its row. */
function BuildLogLink({ domain, id }: { domain: string; id: number }) {
  const t = useT();
  return (
    <TextLink to="/apps/$domain/deployments/$id" params={{ domain, id: String(id) }} size="ui" className="inline-flex w-fit items-center gap-1.5">
      <ScrollText aria-hidden="true" className="size-icon-sm" />
      {t("newApp.deploy.buildLog")}
    </TextLink>
  );
}

/** One part of an import: its state as a shape and a word, the server's name for it and why not, verbatim. */
function ReportRow({ step }: { step: ImportStepReport }) {
  const t = useT();
  return (
    <li className="flex items-start gap-2 text-13 text-fg">
      {step.applied ? (
        <ICONS.success aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-ok" />
      ) : (
        <CircleMinus aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-warn" />
      )}
      <span className="flex min-w-0 flex-col gap-0.5">
        <span>
          <span className="sr-only">{`${t(step.applied ? "newApp.deploy.applied" : "newApp.deploy.notApplied")}: `}</span>
          <Mono>{step.part}</Mono>
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
        <div className="flex flex-col gap-2">
          <p id={notAppliedId} className="text-13 font-medium text-fg">
            {t("newApp.deploy.notApplied")}
          </p>
          <p className="text-12 text-pretty text-fg-muted">{t("newApp.deploy.notAppliedHint")}</p>
          <ul aria-labelledby={notAppliedId} className="flex flex-col gap-2">
            {report.notApplied.map((step) => (
              <ReportRow key={step.part} step={step} />
            ))}
          </ul>
        </div>
      ) : null}
      {applied.length > 0 ? (
        <div className="flex flex-col gap-2">
          <p id={appliedId} className="text-13 font-medium text-fg">
            {t("newApp.deploy.applied")}
          </p>
          <ul aria-labelledby={appliedId} className="flex flex-col gap-2">
            {applied.map((step) => (
              <ReportRow key={step.part} step={step} />
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/** A recipe's notes, once it is deployed: what to do next, with every address in them a link. */
function NextSteps({ notes }: { notes: readonly string[] }) {
  const t = useT();
  const id = useId();
  return (
    <div className="flex flex-col gap-2">
      <p id={id} className="text-13 font-medium text-fg">
        {t("newApp.deploy.nextSteps")}
      </p>
      <ol aria-labelledby={id} className="flex list-decimal flex-col gap-2 pl-5 text-13 text-pretty text-fg marker:text-fg-muted">
        {notes.map((note) => (
          <li key={note}>
            <NoteText note={note} />
          </li>
        ))}
      </ol>
    </div>
  );
}

export interface JobOutcomeProps {
  /** A recipe says what to do next; an import says what it applied. */
  kind: "recipe" | "import";
  target: LandingTarget;
  followedJob: FollowedJob;
}

/**
 * A recipe's deploy or an import, followed to its end on the wizard's own page: what comes
 * next is only known once the job has finished (the notes, the parts applied), so it does not
 * hand over to the deployment page as a plain deploy does. The build log is one link away as
 * soon as the deployer records it; opening the application is the wizard's own last action.
 */
export function JobOutcome({ kind, target, followedJob }: JobOutcomeProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const landing = useDeploymentLanding(target, followedJob);
  const job = followedJob.job;
  const finished = job !== null && isJobFinished(job);
  const log = landing?.kind === "deployment" ? <BuildLogLink domain={target.domain} id={landing.id} /> : undefined;

  useEffect(() => {
    // The application exists now, or failed to: the list and the palette read it again.
    if (finished) void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
  }, [finished, queryClient]);

  if (!finished) {
    return (
      <JobProgress
        state={job?.status === "pending" ? "queued" : "running"}
        title={t(kind === "import" ? "newApp.deploy.importing" : "newApp.deploy.deploying", { domain: target.domain })}
        step={job?.current_step ?? null}
        description={t("newApp.deploy.stays")}
        {...(log !== undefined ? { action: log } : {})}
      />
    );
  }

  if (job.status !== "completed") {
    return (
      <div className="flex flex-col gap-3">
        <JobProgress
          state="failed"
          title={t(kind === "import" ? "newApp.deploy.importFailed" : "newApp.deploy.failed", { domain: target.domain })}
          error={{ detail: job.error ?? t("newApp.deploy.failedNoDetail") }}
          hint={t("newApp.deploy.failedAfterHint")}
        />
        {log}
      </div>
    );
  }

  const notes = kind === "recipe" ? (recipeNotesOf(job.result) ?? []) : [];
  const report = kind === "import" ? importReportOf(job.result) : null;
  return (
    <div className="flex flex-col gap-5">
      <JobProgress
        state="succeeded"
        title={t(kind === "import" ? "newApp.deploy.imported" : "newApp.deploy.deployed", { domain: target.domain })}
        {...(log !== undefined ? { action: log } : {})}
      />
      {notes.length > 0 ? <NextSteps notes={notes} /> : null}
      {report !== null ? <Report report={report} /> : null}
    </div>
  );
}
