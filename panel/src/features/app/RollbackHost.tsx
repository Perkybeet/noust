import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

import type { Job } from "../../api/queries/jobs";
import { RollbackDialog } from "./RollbackDialog";
import type { RollbackChoice } from "./RollbackDialog";

interface PageHost {
  /**
   * Opens the question of going back. With a choice it opens on that version (a row's own, a
   * deployment's page); without, on the one before the version that serves.
   */
  openRollback: (choice?: RollbackChoice) => void;
  /** Hands the page a job somebody queued (a retry, a rebuild), so the header follows it like an update. */
  trackJob: (job: Job) => void;
}

const PageHostContext = createContext<PageHost | null>(null);

/** The way to ask for a rollback from anywhere inside an application's page: there is one question. */
export function useRollback(): Pick<PageHost, "openRollback"> {
  const context = useContext(PageHostContext);
  if (context === null) throw new Error("useRollback is used inside an application's page");
  return context;
}

/**
 * The page's follower of jobs: what an action of a tab queues (a retry, a rebuild) shows in the
 * header under the app's state, with its way to the log, exactly as an update does.
 */
export function useTrackJob(): (job: Job) => void {
  const context = useContext(PageHostContext);
  if (context === null) throw new Error("useTrackJob is used inside an application's page");
  return context.trackJob;
}

export interface RollbackHostProps {
  domain: string;
  /** The app's layout; nothing is drawn until it is read. */
  layout: string | undefined;
  /** Follows the job a rollback (or any action of a tab) queues, in the page's header. */
  onJobQueued: (job: Job) => void;
  children: ReactNode;
}

/**
 * Owns the one rollback dialog of an application's page. The header's Roll back, a deployment's
 * menu, the page of a deployment and Diagnose's remedies all ask it, so the question, its
 * consequences and its confirmation are the same wherever it was asked from.
 */
export function RollbackHost({ domain, layout, onJobQueued, children }: RollbackHostProps) {
  const [state, setState] = useState<{ open: boolean; choice: RollbackChoice | undefined }>({ open: false, choice: undefined });
  const openRollback = useCallback((choice?: RollbackChoice) => setState({ open: true, choice }), []);
  const value = useMemo(() => ({ openRollback, trackJob: onJobQueued }), [openRollback, onJobQueued]);
  return (
    <PageHostContext.Provider value={value}>
      {children}
      {layout !== undefined ? (
        <RollbackDialog
          domain={domain}
          layout={layout}
          open={state.open}
          onOpenChange={(open) => setState((current) => ({ ...current, open }))}
          onJobQueued={onJobQueued}
          {...(state.choice !== undefined ? { choice: state.choice } : {})}
        />
      ) : null}
    </PageHostContext.Provider>
  );
}
