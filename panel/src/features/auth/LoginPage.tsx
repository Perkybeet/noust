import { useDocumentTitle } from "../../app/documentTitle";
import { useT } from "../../i18n";
import { LoginForm } from "./LoginForm";
import type { SsoError } from "./sso";

export interface LoginPageProps {
  /** Where to go once signed in; already checked to be a path of this console. */
  next: string;
  /** The operator was sent here because their session ended. */
  expired: boolean;
  /** `token` opens on the access token (the link the installer prints, `?with=token`). */
  initialMode?: "account" | "token";
  /** The ticket an identity provider's answer left (`?sso=`), collected on arrival. */
  sso?: string;
  /** Why a sign-in through an identity provider did not finish (`?sso_error=`). */
  ssoError?: SsoError;
}

/**
 * The sign-in screen (T7): the slab every screen before the console is. A person's account first, in
 * the password form, with the other ways in as three equal doors under it (a passkey, the sign-in of
 * their company where one is configured, and the access token for whoever has no account yet); the
 * link the installer prints opens on the token. What the token is for is said under its field only to
 * someone who came to it from the account form: the installer's link needs no warning.
 */
export function LoginPage({ next, expired, initialMode = "account", sso, ssoError }: LoginPageProps) {
  const t = useT();
  useDocumentTitle(t("auth.area"));

  return (
    <LoginForm
      next={next}
      expired={expired}
      recoveryHelp={initialMode !== "token"}
      {...(initialMode === "token" ? { initialWay: "token" as const } : {})}
      {...(sso !== undefined ? { sso } : {})}
      {...(ssoError !== undefined ? { ssoError } : {})}
    />
  );
}
