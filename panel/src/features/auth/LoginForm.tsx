import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { ArrowLeft, KeyRound, LifeBuoy } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { isApiError } from "../../api/client";
import { login, loginSecondFactor, sessionQuery } from "../../api/queries/auth";
import type { SecondFactorStep, SessionInfo } from "../../api/queries/auth";
import { completeSso, directorySignIn } from "../../api/queries/sso";
import { announce } from "../../app/Announcer";
import { nodeOfConsolePath } from "../../app/nodeRoute";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { signInWithPasskey } from "./passkeys";
import { rememberSignIn } from "./signInFacts";
import type { SignInFacts } from "./signInFacts";
import { SsoButtons, useLocalSignIn, useSsoProviders } from "./SsoButtons";
import type { SsoError } from "./sso";
import { safeNext } from "./session";
import { CeremonyError, browserLimit, conditionalMediationAvailable } from "./webauthn";

/** Which way in: a person's account (the default), or the access token (emergency access). */
export type LoginMode = "account" | "token";

type TokenStep = "token" | "code";

/** The second step of an account's sign-in, once its password was right. */
interface FactorStep extends SecondFactorStep {
  /** What was typed in the first step, to say whose password was accepted. */
  name: string;
  /** The identity provider that opened this step instead of a password, by its button label. */
  via?: string;
}

/** Seconds left of a lockout, counting down to zero once started. */
function useCountdown(): [number, (seconds: number) => void] {
  const [remaining, setRemaining] = useState(0);
  useEffect(() => {
    if (remaining <= 0) return;
    const timer = setTimeout(() => {
      setRemaining((seconds) => seconds - 1);
    }, 1_000);
    return () => {
      clearTimeout(timer);
    };
  }, [remaining]);
  return [remaining, setRemaining];
}

function formatWait(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${String(minutes)}:${String(rest).padStart(2, "0")}`;
}

/**
 * Where a signed-in operator goes: on a server with no accounts yet, arriving at the overview,
 * the offer to create the first ones; otherwise `next`. A deep link is never taken over.
 */
export function afterSignIn(session: SessionInfo | undefined, next: string): { to: "/setup" | "next" } {
  if (next === "/" && session?.grant === "compat" && session.accounts_exist === false) return { to: "/setup" };
  return { to: "next" };
}

export interface LoginFormProps {
  /** Where to go once signed in; already checked to be a path of this console. */
  next: string;
  /** The operator was sent here because their session ended. */
  expired: boolean;
  mode: LoginMode;
  onModeChange: (mode: LoginMode) => void;
  /** The ticket an identity provider's answer left (`?sso=`): collected once, on arrival. */
  sso?: string;
  /** Why a sign-in through an identity provider did not finish (`?sso_error=`). */
  ssoError?: SsoError;
}

/** A lockout of this address, counting down. */
function LockedNotice({ t, remaining }: { t: T; remaining: number }) {
  return (
    <Notice tone="error" title={t("auth.tooManyAttempts")}>
      {t.rich("auth.lockedFor", {
        time: (
          <>
            <span aria-hidden="true">
              <Mono tone="default" className="font-medium">
                {formatWait(remaining)}
              </Mono>
            </span>
            <span className="sr-only">{t("auth.lockedMinutes", { count: Math.ceil(remaining / 60) })}</span>
          </>
        ),
      })}
    </Notice>
  );
}

/** "or", between the passkey and the form. */
function Or({ t }: { t: T }) {
  return (
    <div className="flex items-center gap-3 text-12 text-fg-faint" aria-hidden="true">
      <span className="h-px flex-1 bg-border" />
      {t("auth.passkey.or")}
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}

/**
 * Signing in. A person uses their account in two steps - their username (or the email of the
 * person, when it names one account) and password, then the second factor the server says the
 * account has: a code, or its passkey - or a passkey on its own, which the username field also
 * offers in the browser's autofill. An account without a second factor is let in on its
 * password and sent to set one up. Every refusal of the first step reads the same, whatever
 * was wrong. The access token is emergency access: kept, one link away, with what it means
 * said before it is typed.
 *
 * An identity provider the operator configured is one more way in, a button above the form. Its
 * answer is only the first factor: the server sends the browser back here with a ticket, which
 * is collected on arrival, and an account with a second factor owes it exactly as after a
 * password.
 */
export function LoginForm({ next, expired, mode, onModeChange, sso, ssoError }: LoginFormProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { error: sessionError } = useQuery(sessionQuery());
  const providers = useSsoProviders();
  // With passwords turned off for single sign-on, the account form is a way back, not the way in.
  const localSignIn = useLocalSignIn();
  // A directory is signed in against with a name and a password, in the form; a provider is a
  // button that leaves for it.
  const directories = providers.filter((provider) => provider.protocol === "ldap");
  const buttonProviders = providers.filter((provider) => provider.protocol !== "ldap");
  const [pickedDirectory, setPickedDirectory] = useState<string | null>(null);
  // With passwords turned off, the directory is the way in the form leads with.
  const directoryName = pickedDirectory ?? (!localSignIn ? (directories[0]?.name ?? "") : "");
  const signInDirectory = directories.find((directory) => directory.name === directoryName);
  const [ssoBusy, setSsoBusy] = useState(sso !== undefined);
  const collectedTicket = useRef<string | null>(null);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [token, setToken] = useState("");
  const [tokenStep, setTokenStep] = useState<TokenStep>("token");
  const [factor, setFactor] = useState<FactorStep | null>(null);
  const [masterPasskey, setMasterPasskey] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<"username" | "password" | "token" | "code", string>>>({});
  const [failure, setFailure] = useState<{ title: string; error: unknown; hint?: string } | null>(null);
  const [pending, setPending] = useState(false);
  const [passkeyPending, setPasskeyPending] = useState(false);

  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const tokenRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const factorPasskeyRef = useRef<HTMLButtonElement>(null);
  const autofill = useRef<AbortController | null>(null);

  const [remaining, lockFor] = useCountdown();
  const locked = remaining > 0;
  const passkeysHere = browserLimit() === null;

  useEffect(() => {
    if (expired) announce(t("auth.sessionExpired"));
  }, [expired, t]);

  // The code step (of the access token, or of an account) moves focus to its field: the one
  // thing left to type. An account with only a passkey is offered it instead.
  const factorHasCode = factor !== null && (factor.methods.includes("totp") || factor.methods.includes("backup_code"));
  useEffect(() => {
    if ((mode === "token" && tokenStep === "code") || (mode === "account" && factorHasCode)) codeRef.current?.focus();
    else if (mode === "account" && factor !== null) factorPasskeyRef.current?.focus();
  }, [mode, tokenStep, factor, factorHasCode]);

  // A refused value is selected for retyping once the field is enabled again: the fields are
  // disabled while a request is in flight, and a disabled field drops focus and ignores
  // select(), which left keyboard and screen reader users on <body> after every typo.
  const refusedField = fieldErrors.token !== undefined ? tokenRef : fieldErrors.code !== undefined ? codeRef : null;
  useEffect(() => {
    if (pending || refusedField === null) return;
    refusedField.current?.focus();
    refusedField.current?.select();
  }, [pending, refusedField]);

  const finish = async (answer: SignInFacts, destination: string = next): Promise<void> => {
    rememberSignIn(answer);
    const session = await queryClient.query({ ...sessionQuery(), staleTime: 0 });
    announce(t("auth.signedIn"));
    if (afterSignIn(session, destination).to === "/setup") {
      await navigate({ to: "/setup", search: { next: destination }, replace: true });
      return;
    }
    // `destination` may name a node the address-bar way (`/n/web-2/apps`), which `navigate({ href })`
    // would land on stripped of its node (see nodeOfConsolePath): built as `{ to, search }`
    // instead whenever it does, so a link to a node's page still opens there once its owner
    // has signed in. The console's own guard sends an account with checks pending to them.
    const { node, pathname } = nodeOfConsolePath(destination);
    await navigate(node === null ? { href: destination, replace: true } : { to: pathname, search: { node }, replace: true });
  };

  // The passkey in the username field's autofill (conditional UI), for as long as the account
  // form is on screen. Aborted when the operator does anything else with a passkey or leaves.
  const firstStep = factor === null;
  useEffect(() => {
    if (mode !== "account" || !passkeysHere || !firstStep || ssoBusy) return;
    const state = { live: true };
    // Read through a function: the flag changes while the ceremony waits, which a narrowed
    // read of the property would not see.
    const live = (): boolean => state.live;
    const controller = new AbortController();
    autofill.current = controller;
    void conditionalMediationAvailable().then(async (available) => {
      if (!available || !live()) return;
      try {
        const answer = await signInWithPasskey({ mediation: "conditional", signal: controller.signal });
        await finish(answer);
      } catch (error: unknown) {
        if (!live() || controller.signal.aborted) return;
        if (error instanceof CeremonyError && error.cancelled) return;
        setFailure({ title: t("auth.passkey.failedTitle"), error });
      }
    });
    return () => {
      state.live = false;
      controller.abort();
      if (autofill.current === controller) autofill.current = null;
    };
    // finish is stable in intent (it reads the latest next through its closure on each run).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, passkeysHere, firstStep, ssoBusy]);

  const stopAutofill = (): void => {
    autofill.current?.abort();
    autofill.current = null;
  };

  const lockedOut = (error: { detail: string; retryAfter: number | null }): void => {
    const seconds = error.retryAfter ?? 60;
    lockFor(seconds);
    // The server's own words, then the console's own addition: two sentences, not one built from parts.
    announce(`${error.detail} ${t("auth.tryAgainIn", { time: formatWait(seconds) })}`, "assertive");
  };

  const reset = (): void => {
    setFieldErrors({});
    setFailure(null);
  };

  // The ticket an identity provider's answer left. It is collected once, and taken out of the
  // address first, so it is not left in the history for a back button to offer again.
  useEffect(() => {
    // A ticket is single use: strict mode runs this twice on one page, and the second must not spend it again.
    if (sso === undefined || collectedTicket.current === sso) return;
    collectedTicket.current = sso;
    void (async () => {
      try {
        await navigate({ to: "/login", search: next === "/" ? {} : { next }, replace: true });
        const done = await completeSso({ ticket: sso, bearer: false });
        const step = done.login.second_factor;
        if (step) {
          setCode("");
          setFactor({ ...step, name: done.label, via: done.label });
          const codeFirst = step.methods.includes("totp") || step.methods.includes("backup_code");
          announce(t(codeFirst ? "auth.sso.codeAnnounce" : "auth.sso.passkeyAnnounce", { provider: done.label }));
          return;
        }
        await finish(done.login, safeNext(done.next));
      } catch (error: unknown) {
        if (isApiError(error) && (error.error === "locked_out" || error.error === "rate_limited")) {
          lockedOut(error);
        } else if (isApiError(error) && error.error === "sign_in_expired") {
          setFailure({ title: t("auth.sso.failedTitle"), error: new Error(t("auth.sso.errors.expired")) });
        } else {
          setFailure({ title: t("auth.sso.failedTitle"), error });
        }
      } finally {
        setSsoBusy(false);
      }
    })();
    // The ticket is what runs this, once; the rest is read as it is when the answer arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sso]);

  const withPasskey = async (): Promise<void> => {
    if (pending || passkeyPending || locked) return;
    stopAutofill();
    reset();
    setPasskeyPending(true);
    try {
      await finish(await signInWithPasskey(factor === null ? {} : { challenge: factor.challenge }));
    } catch (error: unknown) {
      if (error instanceof CeremonyError && error.cancelled) return;
      if (isApiError(error) && (error.error === "locked_out" || error.error === "rate_limited")) {
        lockedOut(error);
        return;
      }
      if (isApiError(error) && (error.error === "sign_in_expired" || error.error === "invalid_credentials")) {
        backToPassword(error);
        return;
      }
      setFailure({
        title: t("auth.passkey.failedTitle"),
        error,
        ...(isApiError(error) && error.error === "passkey_unknown" ? { hint: t("auth.passkey.unknownHint") } : {}),
      });
    } finally {
      setPasskeyPending(false);
    }
  };

  const submitAccount = async (event: SyntheticEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (pending || locked) return;
    const errors: typeof fieldErrors = {};
    if (username.trim() === "") errors.username = t("auth.account.enterUsername");
    if (password === "") errors.password = t("auth.account.enterPassword");
    reset();
    if (errors.username !== undefined || errors.password !== undefined) {
      setFieldErrors(errors);
      (errors.username !== undefined ? usernameRef : passwordRef).current?.focus();
      return;
    }
    stopAutofill();
    setPending(true);
    try {
      const name = username.trim();
      const answer =
        signInDirectory === undefined
          ? await login({ username: name, password, bearer: false })
          : await directorySignIn({ directory: signInDirectory.name, username: name, password, bearer: false });
      setPassword("");
      if (answer.second_factor) {
        // The password was right and the account has a second factor: that step is next.
        const step = answer.second_factor;
        setCode("");
        setFactor({ ...step, name, ...(signInDirectory === undefined ? {} : { via: signInDirectory.label }) });
        const codeFirst = step.methods.includes("totp") || step.methods.includes("backup_code");
        announce(codeFirst ? t("auth.account.codeAnnounce") : t("auth.account.passkeyAnnounce"));
        return;
      }
      await finish(answer);
    } catch (error: unknown) {
      if (isApiError(error) && (error.error === "locked_out" || error.error === "rate_limited")) {
        lockedOut(error);
        return;
      }
      setPassword("");
      if (isApiError(error) && (error.error === "sign_in_refused" || error.error === "sign_in_unavailable")) {
        // Not a wrong password: the directory knows the person and this server will not have them,
        // or could not ask. Said in the server's words, without the hint about retyping.
        setFailure({
          title: error.error === "sign_in_refused" ? t("auth.directory.refusedTitle") : t("auth.directory.unavailableTitle"),
          error,
        });
        return;
      }
      setFailure({ title: t("auth.account.failedTitle"), error, hint: t("auth.account.failedHint") });
      passwordRef.current?.focus();
    } finally {
      setPending(false);
    }
  };

  /** Back to the first step, the name kept: the second step is over (spent, expired, refused). */
  const backToPassword = (error?: unknown): void => {
    setFactor(null);
    setCode("");
    setPassword("");
    setFieldErrors({});
    setFailure(error === undefined ? null : { title: t("auth.account.failedTitle"), error });
  };

  // Once the first step is back on screen, the password is what is left to type.
  const wasFactor = useRef(false);
  useEffect(() => {
    if (factor === null && wasFactor.current) passwordRef.current?.focus();
    wasFactor.current = factor !== null;
  }, [factor]);

  const submitFactor = async (event: SyntheticEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (pending || locked || factor === null) return;
    const value = code.replace(/\s+/g, "");
    if (value === "") {
      setFieldErrors({ code: t("auth.enterCode") });
      codeRef.current?.focus();
      return;
    }
    reset();
    setPending(true);
    try {
      await finish(await loginSecondFactor({ challenge: factor.challenge, code: value, bearer: false }));
    } catch (error: unknown) {
      if (!isApiError(error)) {
        setFailure({ title: t("auth.signInFailed"), error });
        return;
      }
      switch (error.error) {
        case "invalid_totp":
          setFieldErrors({ code: error.detail });
          return;
        case "locked_out":
        case "rate_limited":
          lockedOut(error);
          return;
        case "sign_in_expired":
        case "invalid_credentials":
          backToPassword(error);
          return;
        default:
          setFailure({ title: t("auth.signInFailed"), error });
      }
    } finally {
      setPending(false);
    }
  };

  const submitToken = async (event: SyntheticEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (pending || locked) return;
    const value = tokenStep === "token" ? token : code.trim();
    if (value === "") {
      setFieldErrors(tokenStep === "token" ? { token: t("auth.enterToken") } : { code: t("auth.enterCode") });
      return;
    }
    reset();
    setPending(true);
    try {
      // bearer: false - the browser keeps the session in an HttpOnly cookie and never sees it.
      const answer = await login(tokenStep === "token" ? { token, bearer: false } : { token, bearer: false, totp_code: code.trim() });
      await finish(answer);
    } catch (error: unknown) {
      if (!isApiError(error)) {
        setFailure({ title: t("auth.signInFailed"), error });
        return;
      }
      switch (error.error) {
        case "totp_required":
        case "second_factor_required":
          setMasterPasskey(error.error === "second_factor_required");
          setTokenStep("code");
          announce(t("auth.tokenAcceptedAnnounce"));
          return;
        case "passkey_required":
          // The token alone no longer signs in: its passkey does, on its own.
          setMasterPasskey(true);
          setFailure({ title: t("auth.emergency.passkeyRequiredTitle"), error });
          return;
        case "invalid_token":
          setTokenStep("token");
          setCode("");
          setFieldErrors({ token: error.detail });
          return;
        case "invalid_totp":
          setFieldErrors({ code: error.detail });
          return;
        case "locked_out":
        case "rate_limited":
          lockedOut(error);
          return;
        default:
          setFailure({ title: t("auth.signInFailed"), error });
      }
    } finally {
      setPending(false);
    }
  };

  const switchTo = (next: LoginMode): void => {
    stopAutofill();
    reset();
    setTokenStep("token");
    setFactor(null);
    setCode("");
    setMasterPasskey(false);
    onModeChange(next);
  };

  const unreachable = sessionError !== null && failure === null ? sessionError : null;
  const notices: ReactNode = (
    <>
      {expired ? <Notice title={t("auth.sessionExpired")} /> : null}
      {ssoError !== undefined && failure === null ? (
        <Notice tone="error" live title={t("auth.sso.failedTitle")}>
          {t(`auth.sso.errors.${ssoError}`)}
        </Notice>
      ) : null}
      {locked ? <LockedNotice t={t} remaining={remaining} /> : null}
      {failure !== null ? (
        <ErrorBlock live compact error={failure.error} title={failure.title} {...(failure.hint !== undefined ? { hint: failure.hint } : {})} />
      ) : unreachable !== null ? (
        <ErrorBlock compact error={unreachable} title={t("auth.signInFailed")} />
      ) : null}
    </>
  );

  const passkeyButton = (
    <Button size="lg" className="w-full" icon={<KeyRound aria-hidden="true" />} loading={passkeyPending} disabled={locked || pending} onClick={() => void withPasskey()}>
      {t("auth.passkey.signIn")}
    </Button>
  );

  if (mode === "token") {
    return (
      <div className="flex flex-col gap-5">
        <Notice tone="warning" title={t("auth.emergency.warningTitle")}>
          {t("auth.emergency.warning")}
        </Notice>
        <form onSubmit={(event) => void submitToken(event)} noValidate className="flex flex-col gap-5">
          {notices}
          {/* Password managers file a secret under a user name; the token has none of its own. */}
          <input type="text" name="username" autoComplete="username" value="noust-access-token" readOnly hidden />
          {tokenStep === "token" ? (
            <Field label={t("auth.accessToken")} error={fieldErrors.token}>
              <Input
                ref={tokenRef}
                name="token"
                type="password"
                mono
                autoComplete="current-password"
                autoCapitalize="off"
                spellCheck={false}
                value={token}
                onValueChange={(value: string) => {
                  setToken(value);
                }}
                disabled={pending}
              />
            </Field>
          ) : (
            <>
              <div className="flex items-center justify-between gap-3 rounded-control border border-border bg-bg-sunken py-1.5 pr-1.5 pl-3">
                <span className="text-13 text-fg-muted">
                  {t.rich("auth.tokenAccepted", { status: <span className="text-fg">{t("auth.accepted")}</span> })}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  icon={<ArrowLeft aria-hidden="true" />}
                  onClick={() => {
                    setTokenStep("token");
                    setCode("");
                    reset();
                  }}
                  disabled={pending}
                >
                  {t("auth.useDifferentToken")}
                </Button>
              </div>
              <Field label={t("auth.twoFactorCode")} error={fieldErrors.code} description={t("auth.twoFactorHint")}>
                <Input
                  ref={codeRef}
                  name="totp_code"
                  mono
                  autoComplete="one-time-code"
                  autoCapitalize="off"
                  spellCheck={false}
                  maxLength={32}
                  value={code}
                  onValueChange={(value: string) => {
                    setCode(value);
                  }}
                  disabled={pending}
                />
              </Field>
            </>
          )}
          <Button type="submit" variant="primary" size="lg" loading={pending} disabled={locked} className="w-full">
            {tokenStep === "token" ? t("auth.signIn") : t("auth.verify")}
          </Button>
        </form>
        {masterPasskey && passkeysHere ? passkeyButton : null}
        <Button variant="ghost" size="sm" className="self-start" icon={<ArrowLeft aria-hidden="true" />} onClick={() => switchTo("account")}>
          {t("auth.emergency.back")}
        </Button>
      </div>
    );
  }

  if (factor !== null) {
    const withCode = factorHasCode;
    const backupOnly = withCode && !factor.methods.includes("totp");
    const passkeyStep = factor.methods.includes("passkey") && passkeysHere;
    return (
      <div className="flex flex-col gap-5">
        <form onSubmit={(event) => void submitFactor(event)} noValidate className="flex flex-col gap-5">
          {notices}
          <div className="flex items-center justify-between gap-3 rounded-control border border-border bg-bg-sunken py-1.5 pr-1.5 pl-3">
            <span className="min-w-0 text-13 break-words text-fg-muted">
              {factor.via !== undefined ? t("auth.sso.accepted", { provider: factor.via }) : t("auth.account.passwordAccepted", { name: factor.name })}
            </span>
            <Button
              variant="ghost"
              size="sm"
              icon={<ArrowLeft aria-hidden="true" />}
              onClick={() => {
                backToPassword();
              }}
              disabled={pending || passkeyPending}
            >
              {t("auth.account.differentAccount")}
            </Button>
          </div>
          {withCode ? (
            <>
              <Field
                label={backupOnly ? t("auth.account.backupCode") : t("auth.twoFactorCode")}
                error={fieldErrors.code}
                description={backupOnly ? t("auth.account.backupCodeHint") : t("auth.twoFactorHint")}
              >
                <Input
                  ref={codeRef}
                  name="totp_code"
                  mono
                  autoComplete="one-time-code"
                  autoCapitalize="off"
                  spellCheck={false}
                  maxLength={32}
                  value={code}
                  onValueChange={(value: string) => {
                    setCode(value);
                  }}
                  disabled={pending}
                  className="w-48"
                />
              </Field>
              <Button type="submit" variant="primary" size="lg" loading={pending} disabled={locked || passkeyPending} className="w-full">
                {t("auth.verify")}
              </Button>
            </>
          ) : null}
        </form>
        {passkeyStep ? (
          <>
            {withCode ? <Or t={t} /> : null}
            <Button
              ref={factorPasskeyRef}
              // The one way left when the account has no code to type: then it is the primary action.
              variant={withCode ? "secondary" : "primary"}
              size="lg"
              className="w-full"
              icon={<KeyRound aria-hidden="true" />}
              loading={passkeyPending}
              disabled={locked || pending}
              onClick={() => void withPasskey()}
            >
              {t("auth.account.usePasskey")}
            </Button>
          </>
        ) : null}
      </div>
    );
  }

  if (ssoBusy) {
    // The provider's answer is being collected: nothing to type, and nothing to press twice.
    return (
      <Button size="lg" className="w-full" loading>
        {t("auth.sso.finishing")}
      </Button>
    );
  }

  const accountForm = (showNotices: boolean) => (
    <form onSubmit={(event) => void submitAccount(event)} noValidate className="flex flex-col gap-5">
      {showNotices ? notices : null}
      {directories.length > 0 ? (
        <Field label={t("auth.directory.signInWith")} nativeLabel={false}>
          <Select
            options={[
              { value: "", label: t("auth.directory.thisServer") },
              ...directories.map((directory) => ({ value: directory.name, label: directory.label })),
            ]}
            value={directoryName}
            onValueChange={(value) => {
              setPickedDirectory(value);
            }}
          />
        </Field>
      ) : null}
      <Field label={t("auth.account.username")} error={fieldErrors.username}>
        <Input
          ref={usernameRef}
          name="username"
          mono
          // "webauthn" lets the browser offer this server's passkeys right in the field.
          autoComplete="username webauthn"
          autoCapitalize="off"
          spellCheck={false}
          value={username}
          onValueChange={(value: string) => {
            setUsername(value);
          }}
          disabled={pending}
        />
      </Field>
      <Field label={t("auth.account.password")} error={fieldErrors.password}>
        <Input
          ref={passwordRef}
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onValueChange={(value: string) => {
            setPassword(value);
          }}
          disabled={pending}
        />
      </Field>
      <Button type="submit" variant="primary" size="lg" loading={pending} disabled={locked || passkeyPending} className="w-full">
        {t("auth.signIn")}
      </Button>
    </form>
  );

  if (!localSignIn && buttonProviders.length > 0 && directories.length === 0) {
    // The identity provider is the way in; a password or a passkey is for the accounts that were
    // kept one, so it is offered behind a link and not drawn as an equal choice.
    return (
      <div className="flex flex-col gap-5">
        {notices}
        <div className="flex flex-col gap-3">
          <SsoButtons providers={buttonProviders} next={next} disabled={locked} />
        </div>
        <Disclosure label={t("ssoAdmin.signIn.localLink")}>
          <div className="flex flex-col gap-5">
            {passkeysHere ? (
              <>
                {passkeyButton}
                <Or t={t} />
              </>
            ) : null}
            {accountForm(false)}
          </div>
        </Disclosure>
        <Button variant="ghost" size="sm" className="self-start" icon={<LifeBuoy aria-hidden="true" />} onClick={() => switchTo("token")}>
          {t("auth.emergency.link")}
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      {(passkeysHere && localSignIn) || buttonProviders.length > 0 ? (
        <>
          <div className="flex flex-col gap-3">
            {passkeysHere && localSignIn ? passkeyButton : null}
            <SsoButtons providers={buttonProviders} next={next} disabled={locked} />
          </div>
          <Or t={t} />
        </>
      ) : null}
      {accountForm(true)}
      <Button variant="ghost" size="sm" className="self-start" icon={<LifeBuoy aria-hidden="true" />} onClick={() => switchTo("token")}>
        {t("auth.emergency.link")}
      </Button>
    </div>
  );
}
