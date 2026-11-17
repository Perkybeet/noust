/**
 * "Look for new versions": the one button that asks the registries, and the job it starts. Nothing in the zone asks a registry by
 * itself: the summary and the images list say what the last look found and when, and a look is a thing a person asks for. The main part
 * asks without downloading anything (what `docker pull` asks, the digest behind the tag); a Docker that cannot ask that way (no buildx)
 * says so, and the menu offers to download what moved and compare.
 *
 * With All servers the same button asks the same thing of every server of the fleet: it opens the plan first (which servers take part,
 * which are left out and why), and the look is queued only when that is confirmed. What each server found is said when the job ends.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { dockerKeys } from "../../api/queries/docker";
import { checkImages } from "../../api/queries/dockerChanges";
import { dockerViewKeys } from "../../api/queries/dockerFleet";
import { isJobFinished } from "../../api/queries/jobs";
import { Can } from "../../components/page/permissions";
import { MenuItem } from "../../components/ui/Menu";
import { SplitButton } from "../../components/ui/SplitButton";
import { VERB_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { useJobInHand } from "../jobs/useJobInHand";
import { FleetLookDialog } from "./fleet/FleetLookDialog";
import { FleetLookResult } from "./fleet/FleetLookResult";

export interface LookForNewVersions {
  /** The button, for the header of a page; nothing for whoever may not manage Docker. */
  button: ReactNode;
  /** The look in progress or just ended, to put where the page puts jobs; null when there is none. */
  notice: ReactNode;
  /** The plan of a look on every server, which the page draws once; null on a server's own page and while there is no plan to show. */
  dialog: ReactNode;
}

/**
 * @param options.fleet - The page is the fleet's (All servers): the look is asked of every server, with its plan first.
 */
export function useLookForNewVersions({ fleet = false }: { fleet?: boolean } = {}): LookForNewVersions {
  const t = useT();
  const queryClient = useQueryClient();
  const job = useJobInHand(() => {
    // What the look found is on the summary and in every row of the images, on this server and on the fleet's pages: read it all again.
    void queryClient.invalidateQueries({ queryKey: dockerKeys.all });
    void queryClient.invalidateQueries({ queryKey: dockerViewKeys.all });
  });
  const [asking, setAsking] = useState(false);
  const [planning, setPlanning] = useState<{ pull: boolean } | null>(null);
  const words = fleet
    ? { running: t("docker.versions.fleet.running"), succeeded: t("docker.versions.fleet.succeeded"), failed: t("docker.versions.fleet.failed") }
    : { running: t("docker.versions.running"), succeeded: t("docker.versions.succeeded"), failed: t("docker.versions.failed") };
  const look = async (pull: boolean): Promise<void> => {
    if (fleet) {
      setPlanning({ pull });
      return;
    }
    setAsking(true);
    try {
      const accepted = await checkImages(pull);
      job.track(accepted, words);
    } catch (error) {
      reportActionError(t("docker.versions.couldNot"), error);
    } finally {
      setAsking(false);
    }
  };
  const ended = job.job !== null && isJobFinished(job.job) && job.job.status === "succeeded";
  return {
    notice:
      job.notice === null ? null : (
        <div className="flex min-w-0 flex-col gap-3">
          {job.notice}
          {fleet && ended ? <FleetLookResult result={job.job?.result} /> : null}
        </div>
      ),
    dialog:
      planning === null ? null : (
        <FleetLookDialog
          pull={planning.pull}
          onClose={() => setPlanning(null)}
          onQueued={(accepted) => {
            setPlanning(null);
            job.track(accepted, words);
          }}
        />
      ),
    button: (
      <Can permission="docker.manage">
        <SplitButton
          name={t("docker.versions.look")}
          variant="secondary"
          icon={<VERB_ICONS.refresh aria-hidden="true" />}
          loading={asking || job.busy}
          onClick={() => void look(false)}
          menu={
            <MenuItem icon={<VERB_ICONS.install />} description={t("docker.versions.pullDescription")} onClick={() => void look(true)}>
              {t("docker.versions.pull")}
            </MenuItem>
          }
        >
          {t("docker.versions.look")}
        </SplitButton>
      </Can>
    ),
  };
}
