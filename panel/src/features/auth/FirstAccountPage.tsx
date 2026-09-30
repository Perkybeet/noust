import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Stepper } from "../../components/page/Stepper";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { splitErrors } from "../settings/formErrors";
import { accountKeys, createAccount } from "../settings/accounts/api";
import type { Account } from "../settings/accounts/api";
import { roleLabel } from "../settings/accounts/roles";
import { authKeys } from "../../api/queries/auth";
import { AuthFrame } from "./AuthFrame";
import { useSignOut } from "./useSignOut";

type Step = "admin" | "security" | "done";

const FIELDS = ["username", "display_name", "person_ref", "password"] as const;

interface AccountFormProps {
  accountRole: "admin" | "security";
  /** The person of the account made before, to say the two should differ. */
  submitLabel: string;
  onCreated: (account: Account) => void;
}

/** One account: its name, who it belongs to, and a password the person changes later. */
function AccountForm({ accountRole, submitLabel, onCreated }: AccountFormProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [person, setPerson] = useState("");
  const [password, setPassword] = useState("");
  const [missing, setMissing] = useState<Partial<Record<"username" | "password", string>>>({});
  const usernameRef = useRef<HTMLInputElement>(null);

  const create = useMutation({
    mutationFn: () =>
      createAccount({
        username: username.trim(),
        role: accountRole,
        password,
        ...(displayName.trim() !== "" ? { display_name: displayName.trim() } : {}),
        ...(person.trim() !== "" ? { person_ref: person.trim() } : {}),
      }),
    onSuccess: (account) => {
      void queryClient.invalidateQueries({ queryKey: accountKeys.list });
      void queryClient.invalidateQueries({ queryKey: authKeys.session });
      onCreated(account);
    },
  });
  const errors = splitErrors(create.error, FIELDS);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: typeof missing = {};
    if (username.trim() === "") found.username = t("auth.firstAccount.enterUsername");
    if (password === "") found.password = t("auth.firstAccount.enterPassword");
    setMissing(found);
    if (found.username !== undefined || found.password !== undefined) {
      usernameRef.current?.focus();
      return;
    }
    if (!create.isPending) create.mutate();
  };

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("auth.firstAccount.failed")} /> : null}
      <Field label={t("auth.firstAccount.username")} description={t("auth.firstAccount.usernameHint")} error={missing.username ?? errors.fields.username}>
        <Input
          ref={usernameRef}
          mono
          // With the new password below, a password manager saves what this account signs in with.
          autoComplete="username"
          autoCapitalize="off"
          spellCheck={false}
          maxLength={64}
          value={username}
          onValueChange={(value: string) => {
            setUsername(value);
          }}
        />
      </Field>
      <Field label={t("auth.firstAccount.displayName")} optional error={errors.fields.display_name}>
        <Input
          autoComplete="off"
          maxLength={128}
          value={displayName}
          onValueChange={(value: string) => {
            setDisplayName(value);
          }}
        />
      </Field>
      <Field label={t("auth.firstAccount.person")} optional description={t("auth.firstAccount.personHint")} error={errors.fields.person_ref}>
        <Input
          type="email"
          autoComplete="off"
          maxLength={254}
          value={person}
          onValueChange={(value: string) => {
            setPerson(value);
          }}
        />
      </Field>
      <Field label={t("auth.firstAccount.password")} description={t("auth.firstAccount.passwordHint")} error={missing.password ?? errors.fields.password}>
        <Input
          type="password"
          autoComplete="new-password"
          value={password}
          onValueChange={(value: string) => {
            setPassword(value);
          }}
        />
      </Field>
      <Button type="submit" variant="primary" size="lg" className="w-full" loading={create.isPending}>
        {submitLabel}
      </Button>
    </form>
  );
}

export interface FirstAccountPageProps {
  /** Where "Not now" and "Keep using emergency access" go. */
  next: string;
}

/**
 * The first accounts of a server that has none (T7), for whoever signed in with the access
 * token: an administrator, then - recommended - a separate security officer, who manages
 * accounts and reads the audit log, which an administrator cannot. Then the operator signs
 * in as a person, and the token goes back to being emergency access.
 */
export function FirstAccountPage({ next }: FirstAccountPageProps) {
  const t = useT();
  const navigate = useNavigate();
  const { signOut, pending: signingOut } = useSignOut();
  const [step, setStep] = useState<Step>("admin");
  const [created, setCreated] = useState<Account[]>([]);
  useDocumentTitle(t("auth.firstAccount.documentTitle"));

  const steps = [
    { id: "admin", label: t("auth.firstAccount.stepAdmin") },
    { id: "security", label: t("auth.firstAccount.stepSecurity") },
    { id: "done", label: t("auth.firstAccount.stepDone") },
  ];
  const later = (
    <Button variant="ghost" size="sm" onClick={() => void navigate({ href: next, replace: true })}>
      {step === "done" ? t("auth.firstAccount.keepEmergency") : t("auth.firstAccount.notNow")}
    </Button>
  );
  const first = created[0];

  return (
    <AuthFrame
      title={step === "admin" ? t("auth.firstAccount.adminTitle") : step === "security" ? t("auth.firstAccount.securityTitle") : t("auth.firstAccount.doneTitle")}
      description={
        step === "admin"
          ? t("auth.firstAccount.adminDescription")
          : step === "security"
            ? t("auth.firstAccount.securityDescription")
            : t("auth.firstAccount.doneDescription")
      }
      footer={
        <div className="flex flex-col items-start gap-4">
          {later}
          <CommandHint label={t("auth.firstAccount.fromTerminal")} command={step === "security" ? "noust user create NAME --role security" : "noust user create NAME --role admin"} />
        </div>
      }
    >
      <div className="flex flex-col gap-6">
        <Stepper orientation="horizontal" steps={steps} current={step} />
        {step === "admin" ? (
          <AccountForm
            key="admin"
            accountRole="admin"
            submitLabel={t("auth.firstAccount.createAdmin")}
            onCreated={(account) => {
              setCreated([account]);
              setStep("security");
            }}
          />
        ) : step === "security" ? (
          <div className="flex flex-col gap-5">
            <Notice title={t("auth.firstAccount.whyTitle")}>{t("auth.firstAccount.why")}</Notice>
            <AccountForm
              key="security"
              accountRole="security"
              submitLabel={t("auth.firstAccount.createSecurity")}
              onCreated={(account) => {
                setCreated((previous) => [...previous, account]);
                setStep("done");
              }}
            />
            <Button variant="ghost" size="sm" className="self-start" onClick={() => setStep("done")}>
              {t("auth.firstAccount.skipSecurity")}
            </Button>
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            <ul className="flex flex-col gap-1 text-14 text-fg">
              {created.map((account) => (
                <li key={account.username}>
                  {t.rich("auth.firstAccount.createdLine", { name: <Mono key="name">{account.username}</Mono>, role: roleLabel(t, account.role) })}
                </li>
              ))}
            </ul>
            {first !== undefined ? (
              <Button variant="primary" size="lg" className="w-full" loading={signingOut} onClick={() => void signOut()}>
                {t("auth.firstAccount.signInAs", { name: first.username })}
              </Button>
            ) : null}
          </div>
        )}
      </div>
    </AuthFrame>
  );
}
