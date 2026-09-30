import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import { useDocumentTitle } from "../../app/documentTitle";
import { ErrorBlock } from "../../components/page/QueryState";
import { Stepper } from "../../components/page/Stepper";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { splitErrors } from "../settings/formErrors";
import { roleLabel } from "../settings/accounts/roles";
import { AuthFrame } from "./AuthFrame";
import { BackupCodesPanel } from "./BackupCodes";
import { NoticeText } from "./NoticeText";
import { TotpSetup } from "./TotpSetup";

type Opened = ResponseOf<"/api/auth/invitations/open", "post">;
type Step = "code" | "account" | "codes";

/** The code an invitation link carries after `#` (never sent to the server by the browser). */
export function codeFromHash(hash: string): string {
  const raw = hash.replace(/^#/, "");
  const value = raw.startsWith("code=") ? raw.slice("code=".length) : raw;
  try {
    return decodeURIComponent(value).trim();
  } catch {
    return value.trim();
  }
}

const FIELDS = ["password", "totp_code"] as const;

/**
 * Accepting an invitation (T7), with no account yet: the code (from the link or typed), then
 * the person's own password, their authenticator and the usage notice, then the backup codes,
 * once. Nobody else ever knows the password: an invitation is how a security officer lets a
 * person in without choosing it for them.
 */
export function InvitePage() {
  const t = useT();
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("code");
  const [code, setCode] = useState(() => codeFromHash(typeof window === "undefined" ? "" : window.location.hash));
  const [opened, setOpened] = useState<Opened | null>(null);
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [totp, setTotp] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [local, setLocal] = useState<Partial<Record<"code" | "again" | "notice" | "password" | "totp", string>>>({});
  const [codes, setCodes] = useState<readonly string[]>([]);
  const [saved, setSaved] = useState(false);
  const [nudge, setNudge] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  useDocumentTitle(t("auth.invite.documentTitle"));

  // The code is in the address only until it is read: a shared screen or a history entry
  // should not keep it.
  useEffect(() => {
    if (window.location.hash !== "") window.history.replaceState(window.history.state, "", window.location.pathname);
  }, []);

  const open = useMutation({
    mutationFn: () => request("post", "/api/auth/invitations/open", { body: { code: code.trim() } }),
    onSuccess: (result) => {
      setOpened(result);
      setStep("account");
    },
  });
  const accept = useMutation({
    mutationFn: () =>
      request("post", "/api/auth/invitations/accept", {
        body: {
          code: code.trim(),
          password,
          totp_code: totp.replace(/\s+/g, ""),
          ...(opened?.notice ? { notice_version: opened.notice.version } : {}),
        },
      }),
    onSuccess: (result) => {
      setCodes(result.backup_codes);
      setStep("codes");
    },
  });
  const acceptErrors = splitErrors(accept.error, FIELDS);

  const submitCode = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (code.trim() === "") {
      setLocal({ code: t("auth.invite.enterCode") });
      codeRef.current?.focus();
      return;
    }
    setLocal({});
    if (!open.isPending) open.mutate();
  };

  const submitAccount = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: typeof local = {};
    if (password === "") found.password = t("auth.invite.enterPassword");
    else if (password !== again) found.again = t("auth.invite.mismatch");
    if (totp.trim() === "") found.totp = t("auth.invite.enterTotp");
    if (opened?.notice && !accepted) found.notice = t("auth.gate.noticeMissing");
    setLocal(found);
    if (Object.keys(found).length > 0) {
      if (found.password !== undefined || found.again !== undefined) passwordRef.current?.focus();
      return;
    }
    if (!accept.isPending) accept.mutate();
  };

  const steps = [
    { id: "code", label: t("auth.invite.stepCode") },
    { id: "account", label: t("auth.invite.stepAccount") },
    { id: "codes", label: t("auth.invite.stepCodes") },
  ];
  const hostname = window.location.hostname;

  return (
    <AuthFrame
      title={step === "code" ? t("auth.invite.title") : step === "account" ? t("auth.invite.accountTitle") : t("auth.invite.codesTitle")}
      description={
        step === "code"
          ? t("auth.invite.description")
          : step === "account" && opened !== null
            ? t.rich("auth.invite.accountDescription", { name: <Mono key="name">{opened.username}</Mono>, role: roleLabel(t, opened.role) })
            : t("auth.invite.codesDescription")
      }
    >
      <div className="flex flex-col gap-6">
        <Stepper orientation="horizontal" steps={steps} current={step} />
        {step === "code" ? (
          <form noValidate onSubmit={submitCode} className="flex flex-col gap-5">
            {open.isError ? <ErrorBlock live compact error={open.error} title={t("auth.invite.openFailed")} /> : null}
            <Field label={t("auth.invite.codeLabel")} description={t("auth.invite.codeHint")} error={local.code}>
              <Input
                ref={codeRef}
                mono
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                value={code}
                onValueChange={(value: string) => {
                  setCode(value);
                }}
              />
            </Field>
            <Button type="submit" variant="primary" size="lg" className="w-full" loading={open.isPending}>
              {t("auth.invite.open")}
            </Button>
          </form>
        ) : step === "account" && opened !== null ? (
          <form noValidate onSubmit={submitAccount} className="flex flex-col gap-6">
            {acceptErrors.form !== null ? <ErrorBlock live compact error={acceptErrors.form} title={t("auth.invite.acceptFailed")} /> : null}
            <fieldset className="flex flex-col gap-5">
              <legend className="mb-3 text-14 font-medium text-fg">{t("auth.invite.passwordLegend")}</legend>
              {/* A password manager files the new password under the name it signs in with. */}
              <input type="text" name="username" autoComplete="username" value={opened.username} readOnly hidden />
              <Field
                label={t("auth.invite.password")}
                description={t("auth.invite.passwordHint", { count: opened.password_min_length })}
                error={local.password ?? acceptErrors.fields.password}
              >
                <Input
                  ref={passwordRef}
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  onValueChange={(value: string) => {
                    setPassword(value);
                  }}
                />
              </Field>
              <Field label={t("auth.invite.again")} error={local.again}>
                <Input
                  type="password"
                  autoComplete="new-password"
                  value={again}
                  onValueChange={(value: string) => {
                    setAgain(value);
                  }}
                />
              </Field>
            </fieldset>
            <fieldset className="flex flex-col gap-3">
              <legend className="mb-3 text-14 font-medium text-fg">{t("auth.invite.factorLegend")}</legend>
              <TotpSetup layout="narrow" uri={opened.totp_uri} secret={opened.totp_secret}>
                <Field label={t("settings.security.twoFactor.enroll.codeLabel")} error={local.totp ?? acceptErrors.fields.totp_code}>
                  <Input
                    mono
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    spellCheck={false}
                    maxLength={8}
                    value={totp}
                    onValueChange={(value: string) => {
                      setTotp(value);
                    }}
                    className="w-40"
                  />
                </Field>
              </TotpSetup>
            </fieldset>
            {opened.notice ? (
              <fieldset className="flex flex-col gap-3">
                <legend className="mb-3 text-14 font-medium text-fg">{t("auth.gate.noticeTitle")}</legend>
                <NoticeText text={opened.notice.text} label={t("auth.gate.noticeLabel")} />
                <Checkbox
                  label={t("auth.gate.noticeCheckbox")}
                  checked={accepted}
                  onCheckedChange={(next) => {
                    setAccepted(next);
                  }}
                />
                {local.notice !== undefined ? (
                  <Notice tone="warning" live>
                    {local.notice}
                  </Notice>
                ) : null}
              </fieldset>
            ) : null}
            <Button type="submit" variant="primary" size="lg" className="w-full" loading={accept.isPending}>
              {t("auth.invite.activate")}
            </Button>
          </form>
        ) : (
          <div className="flex flex-col gap-5">
            <BackupCodesPanel
              codes={codes}
              hostname={hostname}
              saved={saved}
              nudge={nudge}
              onSavedChange={(next) => {
                setSaved(next);
                if (next) setNudge(false);
              }}
            />
            <Button
              variant="primary"
              size="lg"
              className="w-full"
              onClick={() => {
                if (!saved) {
                  setNudge(true);
                  return;
                }
                void navigate({ to: "/login", replace: true });
              }}
            >
              {t("auth.invite.toSignIn")}
            </Button>
          </div>
        )}
      </div>
    </AuthFrame>
  );
}
