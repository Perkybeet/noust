import { useQuery } from "@tanstack/react-query";
import { LockKeyhole } from "lucide-react";

import { sessionQuery } from "../../api/queries/auth";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { Logo } from "../../components/brand/Logo";
import { Button } from "../../components/ui/Button";
import { useT } from "../../i18n";
import { UnlockForm } from "./UnlockForm";

export interface CentralLockScreenProps {
  /** The operator chose to use the console without its servers. */
  onContinue: () => void;
}

/**
 * What a sealed central shows after sign-in until it is unlocked: why its servers are out of
 * reach, the passphrase that brings them back, and the way on without them. It names the
 * machine first, like the sign-in screen, so a passphrase is never typed into the wrong one.
 */
export function CentralLockScreen({ onContinue }: CentralLockScreenProps) {
  const t = useT();
  useDocumentTitle(t("servers.lock.title"));
  const { data: session } = useQuery(sessionQuery());

  return (
    <main className="flex min-h-dvh items-center justify-center bg-bg px-6 py-12">
      <div className="w-full max-w-[26rem]">
        <div className="mb-12 flex items-center gap-3">
          <Logo variant="icon" height={28} />
          <span aria-hidden="true" className="h-7 w-px bg-border" />
          <div className="flex min-w-0 flex-col gap-0.5">
            <span translate="no" className="mono truncate text-13 font-medium text-fg">
              {session?.hostname ?? "Noust"}
            </span>
            <span className="text-12 text-fg-faint">
              {t("shell.area")}
              {session ? <span className="mono">{` ${session.version}`}</span> : null}
            </span>
          </div>
        </div>
        <div className="mb-3 flex size-10 items-center justify-center rounded-control border border-border bg-surface text-fg-muted shadow-raised">
          <LockKeyhole aria-hidden="true" className="size-5" />
        </div>
        <h1 className="title text-24 text-fg">{t("servers.lock.title")}</h1>
        <p className="mt-1.5 mb-7 text-14 text-pretty text-fg-muted">{t("servers.lock.description")}</p>
        <UnlockForm focusOnMount />
        <p className="mt-4 text-12 text-pretty text-fg-muted">{t("servers.lock.lostPassphrase")}</p>
        <div className="mt-8 flex flex-col gap-2 border-t border-border pt-6">
          <div>
            <Button variant="secondary" onClick={onContinue}>
              {t("servers.lock.continueLocked")}
            </Button>
          </div>
          <p className="text-12 text-pretty text-fg-muted">{t("servers.lock.continueNote")}</p>
          <CommandHint className="mt-3" label={t("servers.lock.fromTerminal")} command="noust central unlock" />
        </div>
      </div>
    </main>
  );
}
