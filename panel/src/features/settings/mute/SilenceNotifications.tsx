import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { configQuery } from "../../../api/queries/config";
import { useNow } from "../../../components/page/clock";
import { useCan } from "../../../components/page/permissions";
import { Button } from "../../../components/ui/Button";
import { VERB_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import { readNotificationSettings } from "../notifications";
import { MuteDialog } from "./MuteDialog";
import { pausedUntil, untilLabel } from "./mute";
import { useResume } from "./useMute";

/** The permission the writes behind it take (the server's, `routes_config.py`). */
const MANAGE = "settings.manage";

/**
 * "Silence the notifications..." for the header of Alerts, where a person who is looking at what fires is the person who
 * wants it quiet for a while: the same dialog as the switch of Notification channels, which is where they are turned off
 * for good. While they are silenced it says until when and turns them on again. Nothing is drawn for a role that may
 * not change settings, or while notifications are off altogether (there is nothing to silence).
 */
export function SilenceNotifications() {
  const t = useT();
  const allowed = useCan(MANAGE);
  const query = useQuery({ ...configQuery(), enabled: allowed });
  const resume = useResume();
  const now = useNow(() => 30_000);
  const [asking, setAsking] = useState(false);
  if (!allowed || query.data === undefined) return null;
  const settings = readNotificationSettings(query.data.config);
  if (!settings.enabled) return null;
  const until = pausedUntil(settings.mutedUntil, new Date(now));
  const state = { enabled: settings.enabled, mutedUntil: settings.mutedUntil };
  if (until !== null) {
    return (
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-13 text-fg-muted">{t("settings.notifications.mute.silencedUntil", { when: untilLabel(until, new Date(now)) })}</span>
        <Button
          loading={resume.isPending}
          onClick={() => {
            resume.mutate(state);
          }}
        >
          {t("settings.notifications.mute.turnOn")}
        </Button>
      </span>
    );
  }
  return (
    <>
      <Button
        icon={<VERB_ICONS.turnOff aria-hidden="true" />}
        onClick={() => {
          setAsking(true);
        }}
      >
        {t("settings.notifications.mute.open")}
      </Button>
      {asking ? (
        <MuteDialog
          state={state}
          onClose={() => {
            setAsking(false);
          }}
        />
      ) : null}
    </>
  );
}
