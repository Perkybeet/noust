import { useMutation } from "@tanstack/react-query";

import { patchConfig } from "../../../api/queries/config";
import { useRefreshConfig } from "../SettingsForm";
import { MUTE_KEY, mutedUntilFor } from "./mute";
import type { MuteChoice } from "./mute";

/** What the notifications hold now, for the two writes below to know what is already so. */
export interface MuteState {
  enabled: boolean;
  /** `notifications.muted_until` as stored. */
  mutedUntil: string;
}

/**
 * Turns every notification off for as long as the person said: a moment (a pause the server ends by itself) or until
 * they turn them on (the master switch). Two writes at most, each through "Confirm it's you" the way every setting is;
 * the second finds the window the first opened.
 */
export function useSilence() {
  const refresh = useRefreshConfig();
  return useMutation({
    mutationFn: async ({ choice, state }: { choice: MuteChoice; state: MuteState }) => {
      if (choice === "off") {
        await patchConfig("notifications.enabled", false);
        // A pause that was already there is over with the switch: turned on again, nothing is left of it.
        if (state.mutedUntil !== "") await patchConfig(MUTE_KEY, "");
      } else {
        await patchConfig(MUTE_KEY, mutedUntilFor(choice, new Date()).toISOString());
      }
      await refresh();
    },
  });
}

/** Turns the notifications on again: the switch, and the pause if there is one. */
export function useResume() {
  const refresh = useRefreshConfig();
  return useMutation({
    mutationFn: async (state: MuteState) => {
      if (!state.enabled) await patchConfig("notifications.enabled", true);
      if (state.mutedUntil !== "") await patchConfig(MUTE_KEY, "");
      await refresh();
    },
  });
}
