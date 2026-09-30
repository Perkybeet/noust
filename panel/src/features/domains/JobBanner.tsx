import { isJobFinished } from "../../api/queries/jobs";
import type { FollowedJob } from "../../api/queries/jobs";
import { JobProgress } from "../../components/page/JobProgress";
import { useT } from "../../i18n";

export interface JobWords {
  /** While it runs: "Extending the certificate to shop.example.com". */
  running: string;
  /** Once it succeeded: "The certificate covers every domain". */
  done: string;
  /** Once it failed: "The certificate was not extended". */
  failed: string;
  /** What to do about a failure, above the job's own words. */
  hint?: string;
}

/**
 * A certificate job this page queued, followed where the operator started it, the way every
 * job in hand is shown (JobProgress): its current step while it runs, a quiet confirmation
 * when it succeeds, and its error verbatim when it fails. Both outcomes stay until dismissed.
 */
export function JobBanner({ followed, words }: { followed: FollowedJob; words: JobWords }) {
  const t = useT();
  const job = followed.job;
  if (followed.id === null) return null;
  if (job === null || !isJobFinished(job)) {
    return <JobProgress state={job?.status === "running" ? "running" : "queued"} title={words.running} step={job?.current_step ?? null} />;
  }
  if (job.status === "completed") {
    return <JobProgress state="succeeded" title={words.done} onDismiss={followed.dismiss} />;
  }
  return (
    <JobProgress
      state="failed"
      title={words.failed}
      error={{ detail: job.error ?? (job.status === "cancelled" ? t("domains.jobBanner.jobCancelled") : t("domains.jobBanner.jobFailedNoReason")) }}
      {...(words.hint !== undefined ? { hint: words.hint } : {})}
      onDismiss={followed.dismiss}
    />
  );
}
