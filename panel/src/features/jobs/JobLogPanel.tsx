import type { ReactNode } from "react";

import type { Job } from "../../api/queries/jobs";
import { JobProgress } from "../../components/page/JobProgress";
import { LogViewer } from "../../components/ui/LogViewer";
import { useT } from "../../i18n";
import { jobLines, jobState, jobStep } from "../databases/jobs";
import type { JobWords } from "./useJobInHand";

export interface JobLogPanelProps {
  /** The job followed; null while the answer that queued it has not arrived. */
  job: Job | null;
  /** What the job says in each state; `queued` is used before the job is known. */
  words: JobWords & { queued: string };
  /** The accessible name of the output. */
  label: string;
  /** What lands under the log once the job has succeeded (a result, a link). */
  result?: ReactNode;
  height?: number;
}

/**
 * A job followed inside a dialog: where it is (waiting, running with its step verbatim, done,
 * or failed with the system's words) and its output as it is printed. The panel the dialogs
 * that start a long job share (importing a dump, publishing the console, enrolling a server).
 */
export function JobLogPanel({ job, words, label, result, height = 220 }: JobLogPanelProps) {
  const t = useT();
  const state = job === null ? "queued" : jobState(job.status);
  const title = job === null ? words.queued : state === "succeeded" ? words.succeeded : state === "failed" ? words.failed : words.running;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <JobProgress
        state={state}
        title={title}
        step={job !== null ? jobStep(job) : null}
        {...(state === "failed" ? { error: { detail: job?.error ?? (job !== null ? jobStep(job) : null) ?? "" } } : {})}
      />
      <LogViewer lines={job !== null ? jobLines(job) : []} label={label} filename={`${job?.id ?? "job"}.log`} height={height} loading={job === null} emptyMessage={t("databases.job.outputEmpty")} />
      {state === "succeeded" ? result : null}
    </div>
  );
}
