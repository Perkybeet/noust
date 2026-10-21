import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "../../../api/client";
import { appKeys } from "../../../api/queries/apps";
import { runtimeKeys } from "../../../api/queries/runtime";
import { announce } from "../../../app/Announcer";
import { useT } from "../../../i18n";
import { useAppActions } from "../../apps/useAppActions";
import type { EnvMerge } from "./draft";

export interface SaveRequest {
  merge: EnvMerge;
  /** Who gets which variable, for the names whose audience changes. */
  audience?: Record<string, string>;
  /** The notes above variables that change (empty text takes one away). */
  comments?: Record<string, string>;
  /** Restart the app once the variables are written, so it runs with them. */
  restart: boolean;
}

/**
 * Writes a draft: only the variables it changes (`PATCH .../env`), never the whole file, so
 * saving one variable reads nothing in clear and carries no other. With `restart` the app is
 * restarted afterwards, when something changed that it does not run with yet; without it the
 * change waits as "not applied" in the page's header.
 */
export function useSaveEnvironment(domain: string) {
  const t = useT();
  const queryClient = useQueryClient();
  const { restart } = useAppActions(domain);
  return useMutation({
    mutationFn: ({ merge, audience, comments }: SaveRequest) =>
      request("patch", "/api/apps/{domain}/env", {
        params: { domain },
        body: {
          set: merge.set,
          unset: merge.unset,
          dry_run: false,
          ...(audience !== undefined && Object.keys(audience).length > 0 ? { audience } : {}),
          ...(comments !== undefined && Object.keys(comments).length > 0 ? { comments } : {}),
        },
      }),
    onSuccess: (result, variables) => {
      void queryClient.invalidateQueries({ queryKey: appKeys.env(domain, false) });
      void queryClient.invalidateQueries({ queryKey: runtimeKeys.composeVariables(domain) });
      void queryClient.invalidateQueries({ queryKey: runtimeKeys.pending(domain) });
      if (result.changed.length === 0) {
        announce(t("environment.tab.savedNothing"));
        return;
      }
      if (variables.restart && result.restart_required) {
        restart.mutate();
        announce(t("environment.tab.savedRestarting", { count: result.changed.length }));
      } else {
        announce(t("environment.tab.saved", { count: result.changed.length }));
      }
    },
  });
}
