import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { Button } from "../../components/ui/Button";
import { useT } from "../../i18n";
import { AuthFrame } from "../auth/AuthFrame";
import { UnlockForm } from "./UnlockForm";

export interface CentralLockScreenProps {
  /** The operator chose to use the console without its servers. */
  onContinue: () => void;
}

/**
 * What a sealed central shows after sign-in until it is unlocked (T7): why its servers are out
 * of reach, the passphrase that brings them back, and the way on without them. It names the
 * machine first, like the sign-in screen, so a passphrase is never typed into the wrong one.
 */
export function CentralLockScreen({ onContinue }: CentralLockScreenProps) {
  const t = useT();
  useDocumentTitle(t("auth.lock.title"));

  return (
    <AuthFrame
      title={t("auth.lock.title")}
      description={t("auth.lock.description")}
      footer={
        <div className="flex flex-col items-start gap-2 border-t border-border pt-6">
          <Button onClick={onContinue}>{t("servers.lock.continueLocked")}</Button>
          <p className="text-12 text-pretty text-fg-muted">{t("servers.lock.continueNote")}</p>
          <CommandHint className="mt-3" label={t("servers.lock.fromTerminal")} command="noust central unlock" />
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        <UnlockForm />
        <p className="text-12 text-pretty text-fg-muted">{t("servers.lock.lostPassphrase")}</p>
      </div>
    </AuthFrame>
  );
}
