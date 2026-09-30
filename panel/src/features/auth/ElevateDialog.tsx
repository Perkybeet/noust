import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError } from "../../api/client";
import { authKeys, elevateSession, sessionQuery } from "../../api/queries/auth";
import type { SessionInfo } from "../../api/queries/auth";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";
import { cancelElevation, resolveElevation, useElevationRequested } from "./elevation";
import { elevateWithPasskey, passkeysQuery } from "./passkeys";
import { CeremonyError, browserLimit } from "./webauthn";

export { elevate } from "./elevation";

/** What "Confirm it's you" asks of whoever is signed in. */
export type ElevationFactor = "account" | "code" | "token";

/**
 * A person confirms with their password and a code (or a passkey); the master token with the
 * console's two-factor code when that is on, the token itself otherwise (or its passkey).
 */
export function elevationFactor(session: SessionInfo | undefined): ElevationFactor {
  if (session?.account !== null && session?.account !== undefined) return "account";
  return session?.totp_enabled === true ? "code" : "token";
}

/**
 * "Confirm it's you": the sudo-mode prompt for destructive actions (D5). Mounted once, at the
 * root; opened by the API client when an action answers 403 elevation_required, which then
 * retries the action. A passkey comes first when the one signed in has one: the browser's
 * prompt opens from the button, never on its own.
 */
export function ElevateDialog() {
  const t = useT();
  const open = useElevationRequested();
  const queryClient = useQueryClient();
  const { data: session } = useQuery({ ...sessionQuery(), enabled: open });
  const passkeys = useQuery({ ...passkeysQuery(), enabled: open });
  const factor = elevationFactor(session);
  const hasPasskey = (passkeys.data?.passkeys.length ?? 0) > 0 && passkeys.data?.availability.supported === true && browserLimit() === null;

  const [password, setPassword] = useState("");
  const [value, setValue] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ title: string; error: unknown } | null>(null);
  const [pending, setPending] = useState<"form" | "passkey" | null>(null);
  const firstRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const formId = useId();

  const reset = (): void => {
    setPassword("");
    setValue("");
    setFieldError(null);
    setFailure(null);
  };

  const done = (elevatedUntil: string): void => {
    queryClient.setQueryData(authKeys.session, (current: SessionInfo | undefined) =>
      current ? { ...current, elevated_until: elevatedUntil } : current,
    );
    reset();
    resolveElevation();
  };

  const onOpenChange = (next: boolean): void => {
    if (next || pending !== null) return;
    reset();
    cancelElevation();
  };

  const refused = (error: unknown, title: string): void => {
    if (isApiError(error) && error.sessionExpired) return; // on its way to sign-in, which cancels this
    if (isApiError(error) && (error.status === 401 || error.error === "locked_out")) {
      setFieldError(error.detail);
      (factor === "account" ? codeRef : firstRef).current?.select();
      return;
    }
    setFailure({ title, error });
  };

  const submit = async (event: SyntheticEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (pending !== null || value.trim() === "" || (factor === "account" && password === "")) return;
    setPending("form");
    setFieldError(null);
    setFailure(null);
    try {
      const body = factor === "account" ? { password, code: value.replace(/\s+/g, "") } : factor === "code" ? { code: value.trim() } : { token: value.trim() };
      const { elevated_until } = await elevateSession(body);
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

  const description =
    factor === "account"
      ? hasPasskey
        ? t("auth.elevate.descriptionAccountPasskey")
        : t("auth.elevate.descriptionAccount")
      : factor === "code"
        ? t("auth.elevate.descriptionTotp")
        : t("auth.elevate.descriptionToken");
  const ready = value.trim() !== "" && (factor !== "account" || password !== "");

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="sm"
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
          <Button type="submit" form={formId} variant="primary" loading={pending === "form"} disabled={!ready || pending === "passkey"}>
            {t("auth.elevate.confirm")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {hasPasskey ? (
          <>
            <Button className="w-full" icon={<KeyRound aria-hidden="true" />} loading={pending === "passkey"} disabled={pending === "form"} onClick={() => void withPasskey()}>
              {t("auth.elevate.withPasskey")}
            </Button>
            <p className="text-12 text-fg-muted">{t("auth.elevate.orBelow")}</p>
          </>
        ) : null}
        <form id={formId} onSubmit={(event) => void submit(event)} noValidate className="flex flex-col gap-4">
          {factor === "account" ? (
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
          <Field label={factor === "token" ? t("auth.accessToken") : t("auth.elevate.authenticationCode")} error={fieldError}>
            <Input
              ref={factor === "account" ? codeRef : firstRef}
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
      </div>
    </Dialog>
  );
}
