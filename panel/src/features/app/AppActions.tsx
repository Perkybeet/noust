import { useNavigate } from "@tanstack/react-router";
import { CircleArrowUp, History, Play, RotateCw, Square, Stethoscope } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { ElevationCancelledError } from "../../api/errors";
import type { App } from "../../api/queries/apps";
import type { Job } from "../../api/queries/jobs";
import { appStatus } from "../../components/page/status";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { ICONS } from "../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { SM_UP, useMediaQuery } from "../../components/ui/useMediaQuery";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { hasUnit } from "../apps/AppRowActions";
import { NothingNewDialog } from "../apps/NothingNewDialog";
import { reportActionError, useAppActions } from "../apps/useAppActions";
import { DeleteAppDialog } from "./DeleteAppDialog";
import { RollbackDialog } from "./RollbackDialog";
import { useConfirmItsYou } from "./useDeleteApp";

export interface AppHeaderActions {
  /** Restart, beside the primary action from the small breakpoint up. */
  secondaryActions?: ReactNode;
  /** Update: the one primary action of every tab of the app. */
  primaryAction?: ReactNode;
  /** Everything else, behind "More actions": start or stop, roll back, and delete, last. */
  overflow?: ReactNode;
  /** The dialogs those actions open, rendered by the layout beside the page. */
  dialogs?: ReactNode;
}

/**
 * The app's actions, in the header's slots: Update the primary, Restart beside it, the rest in
 * the overflow menu (Diagnose among them) with Delete last after a separator. On a phone Update stays in view beside
 * "More actions" and Restart moves into the menu. Stopping asks once; deleting asks for the
 * domain to be typed, after "Confirm it's you" when the session is not in sudo mode.
 */
export function useAppHeaderActions(
  domain: string,
  app: App | undefined,
  { busy, onJobQueued }: { busy: boolean; onJobQueued: (job: Job) => void },
): AppHeaderActions {
  const t = useT();
  const { node } = useNode();
  const wide = useMediaQuery(SM_UP);
  const { restart, start, stop, update, rebuildAnyway, nothingNew, dismissNothingNew } = useAppActions(domain, { onJobQueued });
  const confirmItsYou = useConfirmItsYou();
  const navigate = useNavigate();
  const [confirmStop, setConfirmStop] = useState(false);
  const [rollbackOpen, setRollbackOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  // Nothing to act on until the app is read: the header keeps its place meanwhile.
  if (app === undefined) return {};
  const unit = hasUnit(app);
  const running = appStatus(app.status).state === "running";
  // A stack running outside its unit is neither started nor stopped through the unit: starting
  // it skips the rehearsal, and stopping an inactive unit leaves its containers running. The
  // banner's "Hand it back to Noust" is the way.
  const outside = app.status === "running_unmanaged";
  const Delete = ICONS.delete;

  const restartButton = (
    <Button icon={<RotateCw aria-hidden="true" />} loading={restart.isPending} onClick={() => restart.mutate()}>
      {t("appPages.actions.restart")}
    </Button>
  );

  const overflow = (
    <>
      {unit && !wide ? (
        <MenuItem icon={<RotateCw />} disabled={restart.isPending} onClick={() => restart.mutate()}>
          {t("appPages.actions.restart")}
        </MenuItem>
      ) : null}
      {unit && !outside ? (
        running ? (
          <MenuItem icon={<Square />} disabled={stop.isPending} onClick={() => setConfirmStop(true)}>
            {t("appPages.actions.stop")}
          </MenuItem>
        ) : (
          <MenuItem icon={<Play />} disabled={start.isPending} onClick={() => start.mutate()}>
            {t("appPages.actions.start")}
          </MenuItem>
        )
      ) : null}
      <MenuItem icon={<History />} onClick={() => setRollbackOpen(true)}>
        {t("appPages.actions.rollBackEllipsis")}
      </MenuItem>
      {/* Off the tab strip (eight tabs at most), one click away from every tab. */}
      <MenuItem icon={<Stethoscope />} onClick={() => void navigate({ to: "/apps/$domain/diagnose", params: { domain } })}>
        {t("appPages.actions.diagnose")}
      </MenuItem>
      <MenuSeparator />
      <MenuItem
        icon={<Delete />}
        destructive
        onClick={() => {
          // Sudo mode first, so "Confirm it's you" never opens on top of the typed confirmation.
          confirmItsYou().then(
            () => {
              setDeleteOpen(true);
            },
            (error: unknown) => {
              if (!(error instanceof ElevationCancelledError)) reportActionError(t("appPages.actions.deletionCouldNotStart", { domain }), error);
            },
          );
        }}
      >
        {t("appPages.actions.deleteApplication")}
      </MenuItem>
    </>
  );

  const dialogs = (
    <>
      <ConfirmDialog
        open={confirmStop}
        onOpenChange={setConfirmStop}
        friction="simple"
        server={node}
        title={t("appPages.actions.stopTitle", { domain })}
        description={t("appPages.actions.stopDescription")}
        actionLabel={t("appPages.actions.stopApplication")}
        // The outcome, success or failure, is the toast's: the dialog waits for it, then closes.
        onConfirm={() =>
          new Promise<void>((resolve) => {
            stop.mutate(undefined, {
              onSettled: () => {
                resolve();
              },
            });
          })
        }
      />
      <NothingNewDialog
        domain={domain}
        refusal={nothingNew}
        pending={rebuildAnyway.isPending}
        onRebuild={() => rebuildAnyway.mutate()}
        onClose={dismissNothingNew}
      />
      <RollbackDialog domain={domain} layout={app.layout} open={rollbackOpen} onOpenChange={setRollbackOpen} onJobQueued={onJobQueued} />
      <DeleteAppDialog app={app} open={deleteOpen} onOpenChange={setDeleteOpen} />
    </>
  );

  return {
    ...(unit && wide ? { secondaryActions: restartButton } : {}),
    primaryAction: (
      <Button variant="primary" icon={<CircleArrowUp aria-hidden="true" />} loading={update.isPending || busy} onClick={() => update.mutate()}>
        {t("appPages.actions.update")}
      </Button>
    ),
    overflow,
    dialogs,
  };
}
