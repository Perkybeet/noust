import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useState } from "react";

import { sessionQuery } from "../api/queries/auth";
import { ExternalLink } from "../components/ui/ExternalLink";
import { IconButton } from "../components/ui/IconButton";
import { useT } from "../i18n";

const STORAGE_KEY = "noust.renameNotice";
const DISMISSED = "dismissed";

function isDismissed(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === DISMISSED;
  } catch {
    // Storage can be disabled (privacy modes, some embedded browsers): the notice just shows
    // again next load, which costs far less than an operator who cannot dismiss it at all.
    return false;
  }
}

function rememberDismissed(): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, DISMISSED);
  } catch {
    // Not persisted: dismissing still hides it for this page.
  }
}

/**
 * Tells an operator whose server ran WASM before it was renamed that nothing else changed. A
 * slim, non-modal banner at the top of the main content area - `role="status"`, not a dialog,
 * so it never traps or steals focus - shown once per browser until dismissed, which is
 * remembered across reloads.
 */
export function RenameNotice() {
  const t = useT();
  const { data: renamed } = useQuery({ ...sessionQuery(), select: (session) => session.renamed_from_wasm });
  const [dismissed, setDismissed] = useState(isDismissed);

  if (renamed !== true || dismissed) return null;

  return (
    <div role="status" className="mb-6 flex items-start gap-3 rounded-card border border-border bg-surface-raised px-4 py-3.5">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="max-w-[72ch] text-13 text-pretty text-fg">{t("shell.renameNotice.text")}</p>
        <ExternalLink href="https://github.com/Perkybeet/noust/blob/main/docs/UPGRADING-3.0.md">{t("shell.renameNotice.link")}</ExternalLink>
      </div>
      <IconButton
        label={t("shell.renameNotice.dismiss")}
        icon={<X aria-hidden="true" />}
        size="sm"
        tooltip={false}
        onClick={() => {
          rememberDismissed();
          setDismissed(true);
        }}
      />
    </div>
  );
}
