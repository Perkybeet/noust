import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { ssoProvidersQuery } from "../../api/queries/sso";
import { useDocumentTitle } from "../../app/documentTitle";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { splitErrors } from "../settings/formErrors";
import { AuthFrame } from "./AuthFrame";
import { codeFromHash } from "./InvitePage";

const FIELDS = ["password", "second_factor"] as const;

/**
 * Choosing a new password without an administrator (T7): ask for a link by the account's name
 * or address, then use the link from the mail. The link carries its code after the `#`, which
 * the browser never sends to a server, and the page takes it out of the address as soon as it
 * is read. The answer to the request is the same whether or not the account exists, and
 * choosing the password needs a code of the account's second factor as well, so an inbox alone
 * is not an account.
 */
export function ResetPasswordPage() {
  const t = useT();
  const navigate = useNavigate();
  const [code] = useState(() => codeFromHash(typeof window === "undefined" ? "" : window.location.hash));
  const { data: providers } = useQuery(ssoProvidersQuery());
  useDocumentTitle(t("auth.reset.documentTitle"));

  // The code is in the address only until it is read: a shared screen or a history entry
  // should not keep it.
  useEffect(() => {
    if (window.location.hash !== "") window.history.replaceState(window.history.state, "", window.location.pathname);
  }, []);

  const offered = providers?.password_reset ?? false;

  return (
    <AuthFrame
      title={code === "" ? t("auth.reset.requestTitle") : t("auth.reset.chooseTitle")}
      description={code === "" ? t("auth.reset.requestDescription") : t("auth.reset.chooseDescription")}
      footer={
        <TextLink to="/login" size="ui">
          {t("auth.reset.backToSignIn")}
        </TextLink>
      }
    >
      {!offered && providers !== undefined ? (
        <Notice tone="info">{t("auth.reset.notOffered")}</Notice>
      ) : code === "" ? (
        <RequestLink />
      ) : (
        <ChoosePassword code={code} onDone={() => void navigate({ to: "/login", replace: true })} />
      )}
    </AuthFrame>
  );
}

/** Step one: the name or the address, and the same answer whoever it is. */
function RequestLink() {
  const t = useT();
  const [identifier, setIdentifier] = useState("");
  const [missing, setMissing] = useState(false);
  const ask = useMutation({
    mutationFn: () => request("post", "/api/auth/password-reset/request", { body: { identifier: identifier.trim() } }),
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (identifier.trim() === "") {
      setMissing(true);
      return;
    }
    setMissing(false);
    if (!ask.isPending) ask.mutate();
  };

  if (ask.isSuccess) {
    return (
      <Notice tone="success" live title={t("auth.reset.sentTitle")}>
        {t("auth.reset.sent", { minutes: ask.data.expires_in_minutes })}
      </Notice>
    );
  }
  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      {ask.isError ? <ErrorBlock live compact error={ask.error} title={t("auth.reset.requestFailed")} /> : null}
      <Field label={t("auth.reset.identifier")} description={t("auth.reset.identifierHint")} error={missing ? t("auth.reset.enterIdentifier") : undefined}>
        <Input
          name="username"
          mono
          autoComplete="username"
          autoCapitalize="off"
          spellCheck={false}
          value={identifier}
          onValueChange={(value: string) => {
            setIdentifier(value);
          }}
        />
      </Field>
      <Button type="submit" variant="primary" size="lg" className="w-full" loading={ask.isPending}>
        {t("auth.reset.send")}
      </Button>
    </form>
  );
}

/** Step two: the password, and a code of the second factor. */
function ChoosePassword({ code, onDone }: { code: string; onDone: () => void }) {
  const t = useT();
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [factor, setFactor] = useState("");
  const [local, setLocal] = useState<Partial<Record<"password" | "again" | "factor", string>>>({});
  const passwordRef = useRef<HTMLInputElement>(null);
  const confirm = useMutation({
    mutationFn: () =>
      request("post", "/api/auth/password-reset/confirm", {
        body: { code, password, second_factor: factor.replace(/\s+/g, "") },
      }),
  });
  const errors = splitErrors(confirm.error, FIELDS);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: typeof local = {};
    if (password === "") found.password = t("auth.reset.enterPassword");
    else if (password !== again) found.again = t("auth.reset.mismatch");
    if (factor.trim() === "") found.factor = t("auth.reset.enterFactor");
    setLocal(found);
    if (Object.keys(found).length > 0) {
      if (found.password !== undefined || found.again !== undefined) passwordRef.current?.focus();
      return;
    }
    if (!confirm.isPending) confirm.mutate();
  };

  if (confirm.isSuccess) {
    return (
      <div className="flex flex-col gap-5">
        <Notice tone="success" live title={t("auth.reset.doneTitle")}>
          {t("auth.reset.done")}
        </Notice>
        <Button variant="primary" size="lg" className="w-full" onClick={onDone}>
          {t("auth.reset.toSignIn")}
        </Button>
      </div>
    );
  }
  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("auth.reset.confirmFailed")} /> : null}
      <Field label={t("auth.reset.password")} error={local.password ?? errors.fields.password}>
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
      <Field label={t("auth.reset.again")} error={local.again}>
        <Input
          type="password"
          autoComplete="new-password"
          value={again}
          onValueChange={(value: string) => {
            setAgain(value);
          }}
        />
      </Field>
      <Field label={t("auth.reset.factor")} description={t("auth.reset.factorHint")} error={local.factor ?? errors.fields.second_factor}>
        <Input
          mono
          autoComplete="one-time-code"
          spellCheck={false}
          maxLength={32}
          value={factor}
          onValueChange={(value: string) => {
            setFactor(value);
          }}
          className="w-48"
        />
      </Field>
      <Button type="submit" variant="primary" size="lg" className="w-full" loading={confirm.isPending}>
        {t("auth.reset.choose")}
      </Button>
    </form>
  );
}
