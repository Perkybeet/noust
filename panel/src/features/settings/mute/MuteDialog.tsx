import { useState } from "react";

import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Dialog } from "../../../components/ui/Dialog";
import { useT } from "../../../i18n";
import { formatClock } from "../../../lib/format";
import { MORNING_HOUR, mutedUntilFor } from "./mute";
import type { MuteChoice } from "./mute";
import { useSilence } from "./useMute";
import type { MuteState } from "./useMute";

export interface MuteDialogProps {
  /** What the notifications hold now. */
  state: MuteState;
  onClose: () => void;
}

/**
 * The question asked when every notification is turned off: until when. Almost always it is "for a while", and a switch
 * that turns them off without asking is how the next real incident is missed, so the common answers are a click each
 * and the last one is the switch's own meaning. Said by the same dialog from Settings and from Alerts.
 */
export function MuteDialog({ state, onClose }: MuteDialogProps) {
  const t = useT();
  const [choice, setChoice] = useState<MuteChoice>("hour");
  const silence = useSilence();
  const now = new Date();
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !silence.isPending) onClose();
      }}
      size="sm"
      title={t("settings.notifications.mute.title")}
      description={t("settings.notifications.mute.description")}
      footer={
        <>
          <Button disabled={silence.isPending} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          <Button
            variant="primary"
            loading={silence.isPending}
            onClick={() => {
              // design-exception: write-without-permission the dialog opens only from the channels' switch and from the button of Alerts, both for settings.manage
              silence.mutate({ choice, state }, { onSuccess: onClose });
            }}
          >
            {t("settings.notifications.mute.action")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {silence.isError ? <ErrorBlock live compact error={silence.error} title={t("settings.notifications.mute.failed")} /> : null}
        <ChoiceCards<MuteChoice>
          legend={t("settings.notifications.mute.legend")}
          columns={1}
          value={choice}
          onValueChange={setChoice}
          options={[
            { value: "hour", label: t("settings.notifications.mute.hour"), description: t("settings.notifications.mute.hourHelp", { when: formatClock(mutedUntilFor("hour", now)) }) },
            { value: "morning", label: t("settings.notifications.mute.morning", { hour: `${String(MORNING_HOUR).padStart(2, "0")}:00` }), description: t("settings.notifications.mute.morningHelp") },
            { value: "off", label: t("settings.notifications.mute.off"), description: t("settings.notifications.mute.offHelp") },
          ]}
        />
      </div>
    </Dialog>
  );
}
