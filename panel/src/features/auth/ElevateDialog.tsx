import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { Suspense, lazy, useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError } from "../../api/client";
import { authKeys, elevateSession, sessionQuery } from "../../api/queries/auth";
import type { ElevateBody, SessionInfo } from "../../api/queries/auth";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { spokenDuration } from "../settings/security";
import { cancelElevation, resolveElevation, useElevationRequested } from "./elevation";
import { usePasswordAsked } from "./identityProof";
import { elevateWithPasskey, passkeysQuery } from "./passkeys";
import { CeremonyError, browserLimit } from "./webauthn";

export { elevate } from "./elevation";

// The whole of the second factor's setup (QR code, passkey ceremony, backup codes) loads when an account without one meets this dialog.
const ElevateEnrol = lazy(() => import("./ElevateEnrol"));

/** What "Confirm it's you" asks of whoever is signed in. */
export type ElevationFactor = "account" | "code" | "token";

export interface ElevationNeeds {
  factor: ElevationFactor;
  /** An account that has no second factor yet: there is nothing to confirm with, and it can set one up here. */
  enrol: boolean;
  /** The password goes with the code: the server asks for it again (`auth.sudo.require_password`). */
  password: boolean;
  /** A code (authenticator or backup) is accepted; false for an account that can only use a passkey. */
  code: boolean;
}

/**
 * What confirming takes, as the session says. A person already gave their password and a code
 * to sign in, so one factor is enough (a passkey, or a code), and the password too only where
 * the server asks for it; the master token confirms with the console's two-factor code when
 * that is on, the token itself otherwise (or its passkey).
 */
export function elevationNeeds(session: SessionInfo | undefined): ElevationNeeds {
  if (session?.account !== null && session?.account !== undefined) {
    const factors = session.elevation_factors ?? [];
    return {
      factor: "account",
      enrol: factors.length === 0 && !session.totp_enabled,
      password: session.elevation_requires_password,
      code: factors.length === 0 || factors.includes("totp") || factors.includes("backup_code"),
    };
  }
  return { factor: session?.totp_enabled === true ? "code" : "token", enrol: false, password: false, code: true };
}

/**
 * "Confirm it's you": the sudo-mode prompt for destructive actions (D5). Mounted once, at the
 * root; opened by the API client when an action answers 403 elevation_required, which then
 * retries the action. A passkey comes first when the one signed in has one: the browser's
 * prompt opens from the button, never on its own. It asks for what the session says confirming
 * takes: a code and, only where the server asks for it again, the password.
 */
export function ElevateDialog() {
  const t = useT();
  const open = useElevationRequested();
  const queryClient = useQueryClient();
  const { data: session } = useQuery({ ...sessionQuery(), enabled: open });
  const passkeys = useQuery({ ...passkeysQuery(), enabled: open });
  const { factor, enrol: enrolling, password: askPassword, code: acceptsCode } = elevationNeeds(session);
  const hasPasskey = (passkeys.data?.passkeys.length ?? 0) > 0 && passkeys.data?.availability.supported === true && browserLimit() === null;

  const [password, setPassword] = useState("");
  const [value, setValue] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ title: string; error: unknown } | null>(null);
  const [pending, setPending] = useState<"form" | "passkey" | null>(null);
  // The factor was set up here a moment ago: the form that follows says so, and what is left to do.
  const [justEnrolled, setJustEnrolled] = useState(false);
  const firstRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const formId = useId();

  const reset = (): void => {
    setPassword("");
    setValue("");
    setFieldError(null);
    setFailure(null);
    setJustEnrolled(false);
  };

  // The factor is on: read the session again, and the dialog becomes the one an account with a factor has. The code
  // that turned it on is spent for confirming, so the next one is asked for; the backup codes took the time to wait.
  const enrolled = async (): Promise<void> => {
    await queryClient.query({ ...sessionQuery(), staleTime: 0 });
    void queryClient.invalidateQueries({ queryKey: authKeys.all });
    setJustEnrolled(true);
  };

  const done = (elevatedUntil: string): void => {
    queryClient.setQueryData(authKeys.session, (current: SessionInfo | undefined) =>
      current ? { ...current, elevated_until: elevatedUntil } : current,
    );
    reset();
    resolveElevation();
  };

  // "Confirm your password" opens over this dialog while a factor is being set up; a press inside it is outside this one.
  const passwordAsked = usePasswordAsked();

  const onOpenChange = (next: boolean): void => {
    if (next || pending !== null || passwordAsked) return;
    reset();
    cancelElevation();
  };

  const refused = (error: unknown, title: string): void => {
    if (isApiError(error) && error.sessionExpired) return; // on its way to sign-in, which cancels this
    if (isApiError(error) && (error.status === 401 || error.error === "locked_out")) {
      setFieldError(error.detail);
      (askPassword ? codeRef : firstRef).current?.select();
      return;
    }
    setFailure({ title, error });
  };

  const bodyFor = (): ElevateBody => {
    if (factor === "token") return { token: value.trim() };
    if (factor === "code") return { code: value.trim() };
    const code = value.replace(/\s+/g, "");
    return askPassword ? { password, code } : { code };
  };

  const submit = async (event: SyntheticEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (pending !== null || value.trim() === "" || (askPassword && password === "")) return;
    setPending("form");
    setFieldError(null);
    setFailure(null);
    try {
      const { elevated_until } = await elevateSession(bodyFor());
      done(elevated_until);
    } catch (error: unknown) {
      refused(error, t("auth.elevate.confirmFailed"));
    } finally {
      setPending(null);
    }
  };

  const withPasskey = async (): Promise<void> => {
    if (pending !== null) return;
    setPending("passkey");
    setFieldError(null);
    setFailure(null);
    try {
      done(await elevateWithPasskey());
    } catch (error: unknown) {
      if (error instanceof CeremonyError && error.cancelled) return;
      if (isApiError(error) && error.sessionExpired) return;
      setFailure({ title: t("auth.elevate.passkeyFailed"), error });
    } finally {
      setPending(null);
    }
  };

  const description = enrolling
    ? t("auth.elevate.enrolDescription")
    : factor === "account"
      ? askPassword
        ? hasPasskey
          ? t("auth.elevate.descriptionAccountPasskey")
          : t("auth.elevate.descriptionAccount")
        : !acceptsCode
          ? t("auth.elevate.descriptionPasskeyOnly")
          : hasPasskey
            ? t("auth.elevate.descriptionCodePasskey")
            : t("auth.elevate.descriptionCode")
      : factor === "code"
        ? t("auth.elevate.descriptionCode")
        : t("auth.elevate.descriptionToken");
  const ready = value.trim() !== "" && (!askPassword || password !== "");
  // The server's own numbers: what the operator configured, or the ENS profile's.
  const idleMinutes = session?.elevation_idle_minutes;
  const maxMinutes = session?.elevation_max_minutes;
  // Read only when the dialog is open: it is mounted for the whole session, and its sentence uses the
  // duration words of Settings, which a page that never opens it should not have to wait for.
  const keepsOpen =
    open && typeof idleMinutes === "number" && typeof maxMinutes === "number"
      ? t("auth.elevate.keepsOpen", { max: spokenDuration(maxMinutes * 60, t.locale), idle: spokenDuration(idleMinutes * 60, t.locale) })
      : null;
  const showForm = !enrolling && (factor !== "account" || acceptsCode);

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size={enrolling ? "md" : "sm"}
      initialFocus={firstRef}
      title={t("auth.elevate.title")}
      description={description}
      footer={
        <>
          <Button
            disabled={pending !== null}
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("auth.elevate.cancel")}
          </Button>
          {showForm ? (
            <Button type="submit" form={formId} variant="primary" loading={pending === "form"} disabled={!ready || pending === "passkey"}>
              {t("auth.elevate.confirm")}
            </Button>
          ) : null}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {enrolling && session !== undefined ? (
          // Nothing to confirm with yet: the same way of setting a factor up as everywhere, then the dialog goes on.
          <Suspense fallback={<Skeleton className="h-40 w-full rounded-card" />}>
            <ElevateEnrol session={session} onDone={enrolled} />
          </Suspense>
        ) : null}
        {justEnrolled && !enrolling ? <Notice tone="success">{t("auth.elevate.enrolDone")}</Notice> : null}
        {hasPasskey && !enrolling ? (
          <>
            <Button className="w-full" icon={<KeyRound aria-hidden="true" />} loading={pending === "passkey"} disabled={pending === "form"} onClick={() => void withPasskey()}>
              {t("auth.elevate.withPasskey")}
            </Button>
            {showForm ? <p className="text-12 text-fg-muted">{t("auth.elevate.orBelow")}</p> : null}
          </>
        ) : null}
        {showForm ? (
          <form id={formId} onSubmit={(event) => void submit(event)} noValidate className="flex flex-col gap-4">
            {askPassword ? (
              <Field label={t("auth.account.password")}>
                <Input
                  ref={firstRef}
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onValueChange={(next: string) => {
                    setPassword(next);
                  }}
                  disabled={pending !== null}
                />
              </Field>
            ) : null}
            <Field
              label={factor === "token" ? t("auth.accessToken") : t("auth.elevate.authenticationCode")}
              error={fieldError}
              // A code that just signed in is refused here: say so, or it reads as a wrong code.
              {...(fieldError !== null && fieldError !== "" && factor !== "token" ? { description: t("auth.elevate.codeAlreadyUsed") } : {})}
            >
              <Input
                ref={askPassword ? codeRef : firstRef}
                mono
                {...(factor === "token" ? { autoComplete: "current-password", type: "password" } : { autoComplete: "one-time-code", type: "text" })}
                autoCapitalize="off"
                spellCheck={false}
                value={value}
                onValueChange={(next: string) => {
                  setValue(next);
                }}
                disabled={pending !== null}
              />
            </Field>
            {failure !== null ? <ErrorBlock live compact error={failure.error} title={failure.title} /> : null}
          </form>
        ) : failure !== null ? (
          <ErrorBlock live compact error={failure.error} title={failure.title} />
        ) : null}
        {keepsOpen !== null && !enrolling ? <p className="text-12 text-fg-muted">{keepsOpen}</p> : null}
      </div>
    </Dialog>
  );
}
