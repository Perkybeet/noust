import { useNavigate } from "@tanstack/react-router";
import { ArrowRightLeft, CircleArrowUp, Copy, PackageCheck, Play, RotateCw, Square, Stethoscope } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { ElevationCancelledError } from "../../api/errors";
import type { App } from "../../api/queries/apps";
import type { Job } from "../../api/queries/jobs";
import { appStatus } from "../../components/page/status";
import { Button } from "../../components/ui/Button";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { SplitButton } from "../../components/ui/SplitButton";
import { SM_UP, useMediaQuery } from "../../components/ui/useMediaQuery";
import { useT } from "../../i18n";
import { hasUnit } from "../apps/AppRowActions";
import { isAdopted } from "./adopted";
import { useCloneApp, usePromoteApp } from "../promotion/useAppTransfers";
import { FrozenDeployDialog } from "../apps/FrozenDeployDialog";
import { NothingNewDialog } from "../apps/NothingNewDialog";
import { reportActionError, useAppActions } from "../apps/useAppActions";
import { DeleteAppDialog } from "./DeleteAppDialog";
import { useRollback } from "./RollbackHost";
import { StopAppDialog } from "./StopAppDialog";
import { useHasPermission } from "../terminal/queries";
import { useConfirmItsYou } from "./useDeleteApp";
import { useStageRelease } from "./useStagedRelease";
import { OBJECT_ICONS, VERB_ICONS } from "../../components/ui/objectIcons";

export interface AppHeaderActions {
  /** The daily verbs beside the primary action: Restart, Stop or Start, Roll back. */
  secondaryActions?: ReactNode;
  /** Update, with its variations in its menu: the one primary action of every tab of the app. */
  primaryAction?: ReactNode;
  /** What is rare, behind "More actions": Diagnose, Terminal, and Delete last after a separator. */
  overflow?: ReactNode;
  /** The dialogs those actions open, rendered by the layout beside the page. */
  dialogs?: ReactNode;
}

interface VerbProps {
  /** Wide enough for words; below that the verb is an icon that keeps its name. */
  wide: boolean;
  label: string;
  icon: ReactNode;
  loading?: boolean;
  onClick: () => void;
}

/**
 * One daily verb of the header. A phone has no room for four words beside the title, and the
 * verbs are what an operator opens an app on a phone to press (checklist M-31): they stay on
 * screen as icons whose name is their label, never behind the menu.
 */
function Verb({ wide, label, icon, loading = false, onClick }: VerbProps) {
  if (wide) {
    return (
      <Button icon={icon} loading={loading} onClick={onClick}>
        {label}
      </Button>
    );
  }
  return <IconButton variant="secondary" label={label} icon={icon} disabled={loading} onClick={onClick} />;
}

/**
 * The app's actions, in the header's slots. The daily verbs are always on screen, in this order:
 * Restart (the same release, again), Stop or Start by state, Roll back (an earlier release),
 * then Update, the one primary (a new release), whose menu holds the same action done another way:
 * without the cache, or building only. What is rare is in the overflow menu: Diagnose, and Delete
 * last after a separator. A static site has no service, so only Roll back. Stopping
 * asks once; deleting asks for the domain to be typed, after "Confirm it's you" when the
 * session is not in sudo mode. On a phone the verbs are icons with their names (`Verb`).
 */
export function useAppHeaderActions(
  domain: string,
  app: App | undefined,
  { busy, onJobQueued }: { busy: boolean; onJobQueued: (job: Job) => void },
): AppHeaderActions {
  const t = useT();
  const wide = useMediaQuery(SM_UP);
  const { restart, start, stop, update, rebuildAnyway, rebuildFresh, nothingNew, dismissNothingNew, frozen, deployAnyway, dismissFrozen } = useAppActions(domain, { onJobQueued });
  const stage = useStageRelease(domain, onJobQueued);
  const confirmItsYou = useConfirmItsYou();
  const navigate = useNavigate();
  const [confirmStop, setConfirmStop] = useState(false);
  const { openRollback } = useRollback();
  const [deleteOpen, setDeleteOpen] = useState(false);
  // The terminal is offered to whoever may open it; the server decides again when it is asked.
  const canOpenTerminal = useHasPermission("apps.terminal") === true;
  const promote = usePromoteApp(app);
  const clone = useCloneApp(app);

  // Nothing to act on until the app is read: the header keeps its place meanwhile.
  if (app === undefined) return {};
  const unit = hasUnit(app);
  // An adopted site is the operator's: Noust restarts it and reads it, and offers no way to build,
  // roll back or hand over what it did not make.
  const adopted = isAdopted(app);
  const running = appStatus(app.status).state === "running";
  // A stack running outside its unit is neither started nor stopped through the unit: starting
  // it skips the rehearsal, and stopping an inactive unit leaves its containers running. The
  // banner's "Hand it back to Noust" is the way.
  const outside = app.status === "running_unmanaged";
  const Delete = ICONS.delete;

  const secondaryActions = (
    <>
      {unit ? (
        <Verb wide={wide} label={t("appPages.actions.restart")} icon={<RotateCw aria-hidden="true" />} loading={restart.isPending} onClick={() => restart.mutate()} />
      ) : null}
      {unit && !outside ? (
        running ? (
          <Verb wide={wide} label={t("appPages.actions.stop")} icon={<Square aria-hidden="true" />} loading={stop.isPending} onClick={() => setConfirmStop(true)} />
        ) : (
          <Verb wide={wide} label={t("appPages.actions.start")} icon={<Play aria-hidden="true" />} loading={start.isPending} onClick={() => start.mutate()} />
        )
      ) : null}
      {adopted ? null : <Verb wide={wide} label={t("appPages.actions.rollBack")} icon={<OBJECT_ICONS.rollback aria-hidden="true" />} onClick={() => openRollback()} />}
    </>
  );

  const overflow = (
    <>
      {/* Off the tab strip (eight tabs at most), one click away from every tab. */}
      <MenuItem icon={<Stethoscope />} onClick={() => void navigate({ to: "/apps/$domain/diagnose", params: { domain } })}>
        {t("appPages.actions.diagnose")}
      </MenuItem>
      {canOpenTerminal ? (
        <MenuItem icon={<OBJECT_ICONS.terminal />} onClick={() => void navigate({ to: "/apps/$domain/terminal", params: { domain } })}>
          {t("appPages.actions.terminal")}
        </MenuItem>
      ) : null}
      {/* The files are what a shell as the application is: the same permission. */}
      {canOpenTerminal && unit && !adopted ? (
        <MenuItem icon={<OBJECT_ICONS.file />} onClick={() => void navigate({ to: "/apps/$domain/files", params: { domain } })}>
          {t("runtime.files.title")}
        </MenuItem>
      ) : null}
      {promote.available && !adopted ? (
        <MenuItem icon={<ArrowRightLeft />} description={t("promotion.promote.menuDescription")} onClick={promote.open}>
          {t("promotion.promote.menu")}
        </MenuItem>
      ) : null}
      {clone.available && !adopted ? (
        <MenuItem icon={<Copy />} description={t("promotion.clone.menuDescription")} onClick={clone.open}>
          {t("promotion.clone.menu")}
        </MenuItem>
      ) : null}
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
        {adopted ? t("runtime.adopted.stopManaging") : t("appPages.actions.deleteApplication")}
      </MenuItem>
    </>
  );

  const dialogs = (
    <>
      <StopAppDialog domain={domain} open={confirmStop} onOpenChange={setConfirmStop} stop={stop} />
      <NothingNewDialog
        domain={domain}
        refusal={nothingNew}
        pending={rebuildAnyway.isPending}
        onRebuild={() => rebuildAnyway.mutate()}
        onClose={dismissNothingNew}
      />
      <FrozenDeployDialog
        domain={domain}
        refusal={frozen?.refusal ?? null}
        pending={deployAnyway.isPending}
        onDeploy={() => {
          if (frozen !== null) deployAnyway.mutate({ force: frozen.force, noCache: frozen.noCache });
        }}
        onClose={dismissFrozen}
      />
      <DeleteAppDialog app={app} open={deleteOpen} onOpenChange={setDeleteOpen} />
      {promote.dialog}
      {clone.dialog}
    </>
  );

  return {
    secondaryActions,
    ...(adopted
      ? {}
      : {
          primaryAction: (
            <SplitButton
              name={t("appPages.actions.update")}
              variant="primary"
              icon={<CircleArrowUp aria-hidden="true" />}
              loading={update.isPending || busy}
              onClick={() => update.mutate()}
              menu={
                <>
                  <MenuItem icon={<VERB_ICONS.rebuild />} onClick={() => rebuildFresh.mutate()}>
                    {t("appPages.actions.updateFresh")}
                  </MenuItem>
                  {/* Only an app with releases can keep a build waiting beside the one that serves. */}
                  {app.layout === "releases" ? (
                    <MenuItem icon={<PackageCheck />} description={t("rollout.releases.buildOnlyHint")} disabled={stage.isPending} onClick={() => stage.mutate()}>
                      {t("rollout.releases.buildOnly")}
                    </MenuItem>
                  ) : null}
                </>
              }
            >
              {t("appPages.actions.update")}
            </SplitButton>
          ),
        }),
    overflow,
    dialogs,
  };
}
