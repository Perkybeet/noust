import { useQuery } from "@tanstack/react-query";

import { sessionQuery } from "../../api/queries/auth";
import { useDocumentTitle } from "../../app/documentTitle";
import { Logo } from "../../components/brand/Logo";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { LoginForm } from "./LoginForm";
import type { LoginFormProps } from "./LoginForm";

/**
 * The sign-in screen. It names the machine before anything else: an operator with several
 * servers should never type a token into the wrong one.
 */
export function LoginPage({ next, expired }: LoginFormProps) {
  const t = useT();
  useDocumentTitle(t("auth.area"));
  const { data: session, isPending } = useQuery(sessionQuery());

  return (
    <main className="flex min-h-dvh items-center justify-center bg-bg px-6 py-12">
      <div className="w-full max-w-[22.5rem]">
        <div className="mb-12 flex items-center gap-3">
          <Logo variant="icon" height={28} />
          <span aria-hidden="true" className="h-7 w-px bg-border" />
          <div className="flex min-w-0 flex-col gap-0.5">
            {session ? (
              <span translate="no" className="mono truncate text-13 font-medium text-fg">
                {session.hostname}
              </span>
            ) : isPending ? (
              <Skeleton className="h-3.5 w-32" />
            ) : (
              <span className="text-13 font-medium text-fg">Noust</span>
            )}
            <span className="text-12 text-fg-faint">
              {t("shell.area")}
              {session ? <span className="mono">{` ${session.version}`}</span> : null}
            </span>
          </div>
        </div>
        <h1 className="title text-24 text-fg">{t("auth.area")}</h1>
        <p className="mt-1.5 mb-7 text-14 text-pretty text-fg-muted">{t("auth.subtitle")}</p>
        <LoginForm next={next} expired={expired} />
      </div>
    </main>
  );
}
