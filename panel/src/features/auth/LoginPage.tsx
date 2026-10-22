import { useState } from "react";

import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { useT } from "../../i18n";
import { AuthFrame } from "./AuthFrame";
import { LoginForm } from "./LoginForm";
import type { LoginMode } from "./LoginForm";
import type { SsoError } from "./sso";

export interface LoginPageProps {
  /** Where to go once signed in; already checked to be a path of this console. */
  next: string;
  /** The operator was sent here because their session ended. */
  expired: boolean;
  /** Open on the access token (the link the installer prints, `?with=token`). */
  initialMode?: LoginMode;
  /** The ticket an identity provider's answer left (`?sso=`), collected on arrival. */
  sso?: string;
  /** Why a sign-in through an identity provider did not finish (`?sso_error=`). */
  ssoError?: SsoError;
}

/**
 * The sign-in screen (T7). A person's account first, with the access token one button away for
 * whoever has no account yet; the link the installer prints opens on the token. What the token
 * is for is said under its field only to someone who came to it from the account form: the
 * installer's link needs no warning.
 */
export function LoginPage({ next, expired, initialMode = "account", sso, ssoError }: LoginPageProps) {
  const t = useT();
  useDocumentTitle(t("auth.area"));
  const [mode, setMode] = useState<LoginMode>(initialMode);

  return (
    <AuthFrame
      title={mode === "account" ? t("auth.area") : t("auth.token.title")}
      description={mode === "account" ? t("auth.account.description") : t("auth.token.description")}
      footer={
        mode === "account" ? (
          <p className="max-w-measure-help text-12 text-pretty text-fg-muted">{t("auth.account.forgot")}</p>
        ) : (
          <CommandHint label={t("auth.token.lostIt")} command="noust web token" />
        )
      }
    >
      <LoginForm
        next={next}
        expired={expired}
        mode={mode}
        onModeChange={setMode}
        recoveryHelp={initialMode !== "token"}
        {...(sso !== undefined ? { sso } : {})}
        {...(ssoError !== undefined ? { ssoError } : {})}
      />
    </AuthFrame>
  );
}
