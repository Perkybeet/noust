import { useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { Button } from "../ui/Button";
import { ChoiceCards } from "../ui/ChoiceCards";
import type { ChoiceCard } from "../ui/ChoiceCards";
import { Dialog, DialogClose } from "../ui/Dialog";
import { useNow } from "./clock";
import { answerOf, silenceAt } from "./fleetServers";
import type { FleetServer } from "./fleetServers";

export interface WhichServerOption extends FleetServer {
  /** What it is when it can be chosen ("Ubuntu 24.04"). */
  detail?: string;
  /**
   * Why it cannot take what is placed, when it answers but lacks something ("Docker is not installed"): the words come
   * from where the page learned it (the table of what each server can do), never made up here. A server that did not
   * answer, or whose Noust does not know it, says so by itself, before this.
   */
  unfit?: string;
}

export interface WhichServerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** One sentence of what is placed and where: "Choose where Uptime Kuma is installed." The kit adds that the selector stays. */
  description: string;
  /** Every server that could be asked, in the order the page wants. A server that has no business here (a hub that deploys nothing) is left out, not turned off. */
  servers: readonly WhichServerOption[];
  /** The server chosen, when "Continue" is pressed. The dialog has already closed. */
  onChoose: (server: string) => void;
  /** The button's verb, "Continue" by default; a verb when the dialog is the whole step ("Deploy"). */
  confirmLabel?: string;
}

/** Why a server cannot be chosen, in words, or null when it can. The connection first: nothing else of a server can be known without it. */
function reasonOf(option: WhichServerOption, now: number, t: T): string | null {
  const answer = answerOf(option);
  if (answer.kind === "stale" || answer.kind === "unreachable") {
    const age = answer.kind === "stale" ? silenceAt(answer.since, now, t.locale) : null;
    return age === null ? t("fleetCompare.which.offlineNoAge") : t("fleetCompare.which.offline", { age });
  }
  if (answer.kind === "unsupported") {
    return answer.version === undefined || answer.version === null || answer.version === "" ? t("fleetCompare.which.unsupportedNoVersion") : t("fleetCompare.which.unsupported", { version: answer.version });
  }
  return option.unfit ?? null;
}

/**
 * "On which server?", asked when a creation starts with All servers chosen: the button is where it always is and the
 * flow begins here, so the selector is never moved for it (the dialog says so). Every server that is not a fit stays in
 * the list, off, with its reason, so nobody wonders where it went. None is preselected unless exactly one can take it:
 * a deployment on the wrong server by an Enter must not be possible. Continuing with nothing chosen says what is
 * missing and puts the keyboard on the choice; the button is never disabled for it (docs/DESIGN.md 6.7).
 */
export function WhichServerDialog({ open, onOpenChange, description, servers, onChoose, confirmLabel }: WhichServerDialogProps) {
  const t = useT();
  const now = useNow(() => 30_000);
  const choices = useRef<HTMLDivElement>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [asked, setAsked] = useState(false);
  const [wasOpen, setWasOpen] = useState(open);
  // Closing forgets the choice, so the next time it asks again; done while drawing, not after it, so no frame shows the old one.
  if (wasOpen !== open) {
    setWasOpen(open);
    if (!open) {
      setPicked(null);
      setAsked(false);
    }
  }

  const options = useMemo<readonly ChoiceCard<string>[]>(
    () =>
      servers.map((server) => {
        const reason = reasonOf(server, now, t);
        return {
          value: server.name,
          label: server.name,
          untranslated: true,
          description: reason ?? server.detail ?? "",
          disabled: reason !== null,
          ...(server.central === true ? { badge: t("fleetCompare.names.central") } : {}),
        };
      }),
    [servers, now, t],
  );
  const fit = options.filter((option) => option.disabled !== true).map((option) => option.value);
  // A server that stopped answering since it was chosen is no longer the choice.
  const chosen: string | null = picked !== null && fit.includes(picked) ? picked : fit.length === 1 ? (fit[0] ?? null) : null;

  const proceed = (): void => {
    if (chosen === null) {
      setAsked(true);
      choices.current?.querySelector<HTMLElement>("[role='radio']:not([data-disabled])")?.focus();
      return;
    }
    onOpenChange(false);
    onChoose(chosen);
  };

  const body: ReactNode = (
    <div ref={choices} className="flex flex-col gap-3">
      <ChoiceCards<string>
        legend={t("fleetCompare.names.server")}
        options={options}
        value={chosen}
        onValueChange={setPicked}
        columns={1}
        error={asked && chosen === null ? t("fleetCompare.which.choose") : null}
      />
      {fit.length === 0 ? <p className="text-13 text-pretty text-fg-muted">{t("fleetCompare.which.noneFit")}</p> : null}
    </div>
  );

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("fleetCompare.which.title")}
      description={`${description} ${t("fleetCompare.which.staysAsIs")}`}
      footer={
        <>
          <DialogClose render={<Button variant="secondary">{t("common.words.cancel")}</Button>} />
          {fit.length > 0 ? (
            <Button variant="primary" onClick={proceed}>
              {confirmLabel ?? t("common.words.continue")}
            </Button>
          ) : null}
        </>
      }
    >
      {body}
    </Dialog>
  );
}
