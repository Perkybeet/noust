import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "../../api/client";
import { rolloutKey } from "../../api/queries/rollout";
import { announce } from "../../app/Announcer";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";

/**
 * Lets what deploys the app by itself deploy it again (`POST .../rollout/resume`). Said in the
 * words of the Automatic deploys setting, which is where the pause is also made and lifted.
 */
export function useResumeDeploys(domain: string) {
  const t = useT();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/rollout/resume", { params: { domain } }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: rolloutKey(domain) });
      announce(result.held > 0 ? t("rollout.auto.resumedHeld", { domain, count: result.held }) : t("rollout.auto.resumed", { domain }));
    },
    onError: (error) => {
      reportActionError(t("rollout.auto.resumeFailed", { domain }), error);
    },
  });
}
