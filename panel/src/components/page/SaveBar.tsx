import { useRouter } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Button } from "../ui/Button";
import { LeaveGuard } from "./LeaveGuard";
import { useYieldPrimary } from "./primaryYield";

export interface SaveBarProps {
  /** How many fields differ from what is saved. */
  changes: number;
  onDiscard: () => void;
  /** Saves. Leave it out and give `form` to make Save the submit button of that form. */
  onSave?: () => void;
  /** The id of the form Save submits (its validation and its Enter key keep working). */
  form?: string;
  saving?: boolean;
  /**
   * A change is still being applied to the draft: Discard and Save wait for it, or they would
   * act on a draft that is about to change under them.
   */
  busy?: boolean;
  /** "Save" by default; "Test and save" in a file editor. */
  saveLabel?: string;
  /** One more action before Save: "Test" in a file editor. */
  secondary?: ReactNode;
  /**
   * Asks before the page is left with changes (FM-3). On unless the page has its own question
   * (a form that is not saved from here) or the form is a dialog's or a drawer's, which asks
   * when it is closed.
   */
  guard?: boolean;
  className?: string;
}

/**
 * The one way a form is saved: a bar fixed to the bottom of the content that says how many
 * changes are unsaved, with Discard and Save. It is there from the first frame, saying there
 * is nothing to save, so its arrival never pushes the page; Save becomes the view's primary
 * action only while there is something to save, and the header's primary steps aside meanwhile
 * (one primary per view). It also asks once before the page is left with changes: the guard
 * belongs to the bar, so no subsection writes its own.
 */
export function SaveBar({ changes, onDiscard, onSave, form, saving = false, busy = false, saveLabel, secondary, guard = true, className }: SaveBarProps) {
  const t = useT();
  const dirty = changes > 0;
  useYieldPrimary(dirty);
  // A bar drawn alone (a unit test, the gallery) has no router to ask; in the console there always is.
  const routed = Boolean(useRouter({ warn: false }) as ReturnType<typeof useRouter> | null | undefined);
  return (
    <div
      role="region"
      aria-label={t("common.saveBar.region")}
      className={cx(
        "sticky bottom-0 z-sticky flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-border bg-bg py-3",
        className,
      )}
    >
      <p aria-live="polite" className="flex items-center gap-2 text-13">
        {dirty ? (
          <>
            <span aria-hidden="true" className="size-2 rounded-pill bg-warn" />
            <span className="font-medium text-fg">{t("common.saveBar.unsaved", { count: changes })}</span>
          </>
        ) : (
          <span className="text-fg-muted">{t("common.saveBar.noChanges")}</span>
        )}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" disabled={!dirty || saving || busy} onClick={onDiscard}>
          {t("common.saveBar.discard")}
        </Button>
        {secondary}
        <Button
          variant={dirty ? "primary" : "secondary"}
          disabled={!dirty || busy}
          loading={saving}
          {...(form !== undefined ? { type: "submit" as const, form } : {})}
          {...(onSave !== undefined ? { onClick: onSave } : {})}
        >
          {saveLabel ?? t("common.saveBar.save")}
        </Button>
      </div>
      {guard && routed ? <LeaveGuard changes={changes} /> : null}
    </div>
  );
}
