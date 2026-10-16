/**
 * Does what the command palette's verb says, with the application's own machinery (checklist M-25).
 *
 * Each verb is the hook and the dialog its button on the application's page uses: restart and
 * start are `useAppActions`' mutations (a toast says how they went), stop asks `StopAppDialog`'s
 * one question, an update that has nothing new asks `NothingNewDialog`'s, going back asks
 * `RollbackDialog`'s which version. Sudo mode ("Confirm it's you"), permissions and the audit
 * trail are the API client's and the server's, as for a button, because nothing here calls an
 * endpoint of its own. Loaded only when a verb is first chosen, so the console's first paint does
 * not carry the dialogs.
 */

import { useEffect, useRef, useState } from "react";

import { RollbackDialog } from "../features/app/RollbackDialog";
import { StopAppDialog } from "../features/app/StopAppDialog";
import type { AppInfo } from "../features/apps/data";
import { NothingNewDialog } from "../features/apps/NothingNewDialog";
import { useAppActions } from "../features/apps/useAppActions";
import type { AppVerb } from "./verbCommands";

/** Runs `start` once, however many times the effect that calls it is run (Strict Mode runs it twice). */
function useOnce(start: () => void): void {
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    start();
  }, [start]);
}

/** Restart or start: no question, the toast is the answer. */
function UnitVerb({ domain, verb }: { domain: string; verb: "restart" | "start" }) {
  const actions = useAppActions(domain);
  const run = actions[verb];
  useOnce(() => {
    // A failure is reported by the action's own `onError`; the rejection is that same error.
    run.mutateAsync().catch(() => undefined);
  });
  return null;
}

function StopVerb({ domain }: { domain: string }) {
  const { stop } = useAppActions(domain);
  const [open, setOpen] = useState(true);
  return <StopAppDialog domain={domain} open={open} onOpenChange={setOpen} stop={stop} />;
}

/** Queues the update; when the branch has nothing new, asks what the button asks. */
function UpdateVerb({ domain }: { domain: string }) {
  const { update, rebuildAnyway, nothingNew, dismissNothingNew } = useAppActions(domain);
  useOnce(() => {
    update.mutateAsync().catch(() => undefined);
  });
  return <NothingNewDialog domain={domain} refusal={nothingNew} pending={rebuildAnyway.isPending} onRebuild={() => rebuildAnyway.mutate()} onClose={dismissNothingNew} />;
}

function RollbackVerb({ app }: { app: AppInfo }) {
  const [open, setOpen] = useState(true);
  return <RollbackDialog domain={app.domain} layout={app.layout} open={open} onOpenChange={setOpen} />;
}

export interface VerbRunnerProps {
  app: AppInfo;
  verb: AppVerb;
}

/**
 * The verb the palette handed over, run on the application. Mount it with a key that changes for
 * each choice: choosing the same verb twice is two requests.
 */
export function VerbRunner({ app, verb }: VerbRunnerProps) {
  switch (verb) {
    case "restart":
    case "start":
      return <UnitVerb domain={app.domain} verb={verb} />;
    case "stop":
      return <StopVerb domain={app.domain} />;
    case "update":
      return <UpdateVerb domain={app.domain} />;
    case "rollback":
      return <RollbackVerb app={app} />;
  }
}
