/**
 * "Look for new versions": the one button that asks the registries, and the job it starts. Nothing in the zone asks a registry by
 * itself: the summary and the images list say what the last look found and when, and a look is a thing a person asks for. The main part
 * asks without downloading anything (what `docker pull` asks, the digest behind the tag); a Docker that cannot ask that way (no buildx)
 * says so, and the menu offers to download what moved and compare.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { dockerKeys } from "../../api/queries/docker";
import { checkImages } from "../../api/queries/dockerChanges";
import { Can } from "../../components/page/permissions";
import { MenuItem } from "../../components/ui/Menu";
import { SplitButton } from "../../components/ui/SplitButton";
import { VERB_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { useJobInHand } from "../jobs/useJobInHand";

export interface LookForNewVersions {
  /** The button, for the header of a page; nothing for whoever may not manage Docker. */
  button: ReactNode;
  /** The look in progress or just ended, to put where the page puts jobs; null when there is none. */
  notice: ReactNode;
}

export function useLookForNewVersions(): LookForNewVersions {
  const t = useT();
  const queryClient = useQueryClient();
  const job = useJobInHand(() => {
    // What the look found is on the summary and in every row of the images: read it all again.
    void queryClient.invalidateQueries({ queryKey: dockerKeys.all });
  });
  const [asking, setAsking] = useState(false);
  const look = async (pull: boolean): Promise<void> => {
    setAsking(true);
    try {
      const accepted = await checkImages(pull);
      job.track(accepted, { running: t("docker.versions.running"), succeeded: t("docker.versions.succeeded"), failed: t("docker.versions.failed") });
    } catch (error) {
      reportActionError(t("docker.versions.couldNot"), error);
    } finally {
      setAsking(false);
    }
  };
  return {
    notice: job.notice,
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
