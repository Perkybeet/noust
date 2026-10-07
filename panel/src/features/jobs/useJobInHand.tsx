/**
 * The one job a page just queued, followed to its end and drawn where it was started: its step
 * verbatim while it runs, what it did when it ends, its whole output a click away. The pages of
 * the Backups area (an application's data, Noust's own state) each hold one, like a database's
 * page holds its own: starting another waits for this one, and what a finished job changed is
 * read again once.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { isJobFinished, jobKeys, useFollowedJob } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { DatabaseJobProgress } from "../databases/jobs";
import type { Accepted } from "../databases/jobs";

/** The sentence for each state of a job: what it is doing, what it did, what failed. */
export interface JobWords {
  running: string;
  succeeded: string;
  failed: string;
}

export interface JobInHand {
  job: Job | null;
  /** A job this page queued is queued or running. */
  busy: boolean;
  /** Follows a job the page just queued, and names what it does. */
  track: (accepted: Accepted, words: JobWords) => void;
  dismiss: () => void;
  /** The job's progress, to put where jobs go on the page (a notice, a slot); null when there is none. */
  notice: ReactNode;
}

export function useJobInHand(onSettled?: (job: Job) => void): JobInHand {
  const queryClient = useQueryClient();
  const followed = useFollowedJob();
  const [words, setWords] = useState<JobWords | null>(null);
  const job = followed.job;
  const finished = job !== null && isJobFinished(job);

  const settled = useRef<string | null>(null);
  useEffect(() => {
    if (job === null || !isJobFinished(job) || settled.current === job.id) return;
    settled.current = job.id;
    void queryClient.invalidateQueries({ queryKey: jobKeys.active });
    onSettled?.(job);
  }, [job, onSettled, queryClient]);

  const { follow, dismiss: stopFollowing } = followed;
  const track = useCallback(
    (accepted: Accepted, next: JobWords) => {
      setWords(next);
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      const snapshot = accepted.job as Job | undefined;
      follow(snapshot !== undefined && typeof snapshot.id === "string" ? snapshot : accepted.job_id);
    },
    [follow, queryClient],
  );
  const dismiss = useCallback(() => {
    setWords(null);
    stopFollowing();
  }, [stopFollowing]);

  return {
    job,
    busy: followed.id !== null && !finished,
    track,
    dismiss,
    notice: job !== null && words !== null ? <DatabaseJobProgress job={job} words={words} onDismiss={dismiss} /> : null,
  };
}
