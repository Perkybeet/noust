import type { QueueEntry } from "../../api/queries/queue";
import { Button } from "../../components/ui/Button";
import { useT } from "../../i18n";
import type { CancelTarget } from "./useCancelJob";
import { entryDomain, entryTitle } from "./words";
import type { CancelState } from "./words";

export interface CancelJobButtonProps {
  entry: Pick<QueueEntry, "id" | "type" | "name" | "metadata">;
  state: CancelState;
  onAsk: (target: CancelTarget) => void;
  /**
   * The element that says, in words on the page, why the button is disabled: a disabled
   * button explains nothing by itself, and a tooltip is never the only place it is said.
   */
  reasonId?: string;
  className?: string;
}

/**
 * Cancel, for a job that waits, a build that has not started to activate its release, and the
 * states in between: a stop that was asked and is winding down, and a job that cannot be
 * stopped (disabled, with its reason on the page and wired to the button). Pressing it opens
 * the question; it never cancels by itself.
 */
export function CancelJobButton({ entry, state, onAsk, reasonId, className }: CancelJobButtonProps) {
  const t = useT();
  const name = entryTitle(entry, t.locale);
  if (state.kind === "stopping") {
    return (
      <Button size="sm" variant="ghost" loading aria-label={t("queue.cancel.label", { name })} {...(className !== undefined ? { className } : {})}>
        {t("queue.cancel.stopping")}
      </Button>
    );
  }
  const cannot = state.kind === "cannot";
  return (
    <Button
      size="sm"
      variant="ghost"
      disabled={cannot || state.kind === "unknown"}
      aria-label={t("queue.cancel.label", { name })}
      {...(cannot && reasonId !== undefined ? { "aria-describedby": reasonId } : {})}
      {...(className !== undefined ? { className } : {})}
      onClick={() => {
        if (state.kind === "can") onAsk({ id: entry.id, name, domain: entryDomain(entry), stage: state.stage });
      }}
    >
      {t("queue.cancel.button")}
    </Button>
  );
}
