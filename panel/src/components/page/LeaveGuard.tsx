import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { useLeaveGuard } from "../../lib/useLeaveGuard";
import { ConfirmDialog } from "../ui/ConfirmDialog";

export interface LeaveGuardProps {
  /** How many fields differ from what is saved; zero blocks nothing. */
  changes: number;
  /** Replaces the sentence that counts the changes, for a form that has no count to give. */
  description?: ReactNode;
}

/**
 * The one question asked before a form with unsaved changes is left: "Discard unsaved changes?",
 * with a button to keep editing that has the focus, so Enter never loses anything. A `SaveBar`
 * places it; a page whose form has no save bar (the wizard, a file editor) places it itself.
 * Needs the router, as `useLeaveGuard` does.
 */
export function LeaveGuard({ changes, description }: LeaveGuardProps) {
  const t = useT();
  const guard = useLeaveGuard(changes);
  return (
    <ConfirmDialog
      friction="simple"
      open={guard.blocked}
      onOpenChange={(open) => {
        // Both ways out of the dialog end here; once the navigation was let go, staying is a no-op.
        if (!open) guard.stay();
      }}
      title={t("common.leaveGuard.title")}
      description={description ?? t("common.leaveGuard.description", { count: changes })}
      actionLabel={t("common.leaveGuard.discard")}
      cancelLabel={t("common.leaveGuard.keep")}
      onConfirm={() => {
        guard.leave();
        return Promise.resolve();
      }}
    />
  );
}
