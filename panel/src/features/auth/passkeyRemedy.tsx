import { RotateCw } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "../../components/ui/Button";
import type { T } from "../../i18n";
import { CeremonyError } from "./webauthn";

/**
 * What mends a ceremony the browser refuses because it has a passkey request open that nothing on the page can
 * end: a new document. The button beside the browser's words, wherever a ceremony's failure is shown; undefined
 * for any other failure, so the box keeps the actions it had.
 */
export function passkeyRemedy(error: unknown, t: T): ReactNode | undefined {
  if (!(error instanceof CeremonyError) || !error.reload) return undefined;
  return (
    <Button
      size="sm"
      icon={<RotateCw aria-hidden="true" />}
      onClick={() => {
        window.location.reload();
      }}
    >
      {t("auth.passkeyReload")}
    </Button>
  );
}
