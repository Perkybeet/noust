import { useState } from "react";

import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { useT } from "../../i18n";
import { AuthFrame } from "./AuthFrame";
import { LoginForm } from "./LoginForm";
import type { LoginMode } from "./LoginForm";

export interface LoginPageProps {
  /** Where to go once signed in; already checked to be a path of this console. */
  next: string;
  /** The operator was sent here because their session ended. */
  expired: boolean;
  /** Open on the access token (a link from a runbook, `?with=token`). */
  initialMode?: LoginMode;
}

/**
 * The sign-in screen (T7). A person's account first; the access token is emergency access,
 * with its own title and the terminal's way to print it.
 */
export function LoginPage({ next, expired, initialMode = "account" }: LoginPageProps) {
  const t = useT();
  useDocumentTitle(t("auth.area"));
  const [mode, setMode] = useState<LoginMode>(initialMode);

  return (
    <AuthFrame
      title={mode === "account" ? t("auth.area") : t("auth.emergency.title")}
      description={mode === "account" ? t("auth.account.description") : t("auth.emergency.description")}
      footer={
        mode === "account" ? (
          <p className="max-w-measure-help text-12 text-pretty text-fg-muted">{t("auth.account.forgot")}</p>
        ) : (
          <CommandHint label={t("auth.emergency.lostIt")} command="noust web token" />
        )
      }
    >
      <LoginForm next={next} expired={expired} mode={mode} onModeChange={setMode} />
    </AuthFrame>
  );
}
