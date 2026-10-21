import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { authKeys, elevateSession, login, sessionQuery } from "../../api/queries/auth";
import { complianceQuery } from "../../api/queries/compliance";
import type { ComplianceState } from "../../api/queries/compliance";
import { useDocumentTitle } from "../../app/documentTitle";
import { nodeOfConsolePath } from "../../app/nodeRoute";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Stepper } from "../../components/page/Stepper";
import { Button } from "../../components/ui/Button";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { splitErrors } from "../settings/formErrors";
import { accountKeys, createAccount } from "../settings/accounts/api";
import type { Account } from "../settings/accounts/api";
import { takeAccessToken } from "./accessProof";
import { AuthFrame } from "./AuthFrame";
import { SecondFactor } from "./GatePage";
import { pendingChecks } from "./SessionGate";
import { useSignOut } from "./useSignOut";

type Step = "admin" | "security" | "protect";

const FIELDS = ["username", "display_name", "person_ref", "password"] as const;

/** What the administrator typed, kept for the one moment it signs in with it. */
interface Credentials {
  username: string;
  password: string;
}

/** Whether a framework splits an administrator's duties, so the first accounts are two. */
function splitsDuties(state: ComplianceState | undefined): boolean {
  return state?.controls.some((control) => control.id === "roles_split" && Boolean(control.value)) === true;
}

interface AccountFormProps {
  accountRole: "admin" | "security";
  submitLabel: string;
  /** Called with the account made and what was typed to make it. */
  onCreated: (account: Account, credentials: Credentials) => void | Promise<void>;
}

/**
 * One account: its name and a password, which are all it needs. Who it belongs to and what they
 * are called are one disclosure away, because nobody needs them to get in.
 */
function AccountForm({ accountRole, submitLabel, onCreated }: AccountFormProps) {
  const t = useT();
  const queryClient = useQueryClient();
  // A token with a second factor cannot confirm on its own: the dialog asks for the code instead.
  const tokenAlone = useQuery(sessionQuery()).data?.totp_enabled !== true;
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [person, setPerson] = useState("");
  const [password, setPassword] = useState("");
  const [missing, setMissing] = useState<Partial<Record<"username" | "password", string>>>({});
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  const create = useMutation({
    mutationFn: async () => {
      // Creating an account asks to "Confirm it's you" with the token that signed in a moment ago:
      // it is given once, unasked, and if that does not work the ordinary dialog asks.
      const proof = takeAccessToken();
      if (proof !== null && tokenAlone) {
        try {
          await elevateSession({ token: proof });
        } catch {
          // A refusal: the dialog is the way.
        }
      }
      return createAccount({
        username: username.trim(),
        role: accountRole,
        password,
        ...(displayName.trim() !== "" ? { display_name: displayName.trim() } : {}),
        ...(person.trim() !== "" ? { person_ref: person.trim() } : {}),
      });
    },
    onSuccess: async (account) => {
      void queryClient.invalidateQueries({ queryKey: accountKeys.list });
      await onCreated(account, { username: username.trim(), password });
    },
  });
  const errors = splitErrors(create.error, FIELDS);
  // The names the server refused sit in the disclosure: open it, or its message would be hidden.
  const refusedMore = errors.fields.display_name !== undefined || errors.fields.person_ref !== undefined;

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: typeof missing = {};
    if (username.trim() === "") found.username = t("auth.firstAccount.enterUsername");
    if (password === "") found.password = t("auth.firstAccount.enterPassword");
    setMissing(found);
    if (found.username !== undefined || found.password !== undefined) {
      (found.username !== undefined ? usernameRef : passwordRef).current?.focus();
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
      <Field label={t("auth.firstAccount.password")} description={t("auth.firstAccount.passwordHint")} error={missing.password ?? errors.fields.password}>
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
      {/* Keyed by the refusal: a disclosure is opened from the start, so a late refusal remounts it open. */}
      <Disclosure key={refusedMore ? "open" : "closed"} label={t("auth.firstAccount.moreAbout")} defaultOpen={refusedMore}>
        <div className="flex flex-col gap-5">
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
        </div>
      </Disclosure>
      <Button type="submit" variant="primary" size="lg" className="w-full" loading={create.isPending}>
        {submitLabel}
      </Button>
    </form>
  );
}

export interface FirstAccountPageProps {
  /** Where "Not now" and "Set up later" go. */
  next: string;
}

/**
 * The first account of a server that has none (T7), for whoever signed in with the access token:
 * an administrator, with a name and a password and nothing else. Making it signs in with it, so
 * there is no screen that asks the person to sign in again with what they just typed. A framework
 * that separates duties asks for a security officer next, in the same sequence. Then the account is
 * offered its second factor, which it may leave for later unless something requires it (and then
 * the console's own gate has it enrol before anything else).
 */
export function FirstAccountPage({ next }: FirstAccountPageProps) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { signOut, pending: signingOut } = useSignOut();
  const [step, setStep] = useState<Step>("admin");
  const [admin, setAdmin] = useState<Credentials | null>(null);
  const [failedAccount, setFailedAccount] = useState<{ name: string; error: unknown } | null>(null);
  useDocumentTitle(t("auth.firstAccount.documentTitle"));

  // A framework that splits an administrator's duties needs the other account too; asked once,
  // here, and not at all where nothing asks for it.
  const compliance = useQuery({ ...complianceQuery(), retry: false });
  const separateDuties = splitsDuties(compliance.data);
  const session = useQuery(sessionQuery()).data;

  const leave = async (): Promise<void> => {
    const { node, pathname } = nodeOfConsolePath(next);
    await navigate(node === null ? { href: next, replace: true } : { to: pathname, search: { node }, replace: true });
  };

  // The account just made signs in, with what was typed to make it, and the token's session goes.
  const signIn = useMutation({
    mutationFn: async (credentials: Credentials) => {
      await login({ username: credentials.username, password: credentials.password, bearer: false });
      return queryClient.query({ ...sessionQuery(), staleTime: 0 });
    },
    onSuccess: async (fresh) => {
      setAdmin(null);
      void queryClient.invalidateQueries({ queryKey: authKeys.all });
      // Where a framework asks for a second factor, the console's own gate has it enrol first.
      if (pendingChecks(fresh)) {
        await navigate({ to: "/welcome", search: { next }, replace: true });
        return;
      }
      setStep("protect");
    },
  });

  const finish = (credentials: Credentials, name: string): void => {
    setFailedAccount(null);
    signIn.mutate(credentials, {
      onError: (error) => {
        setFailedAccount({ name, error });
      },
    });
  };

  const steps = [
    { id: "admin", label: t("auth.firstAccount.stepAdmin") },
    ...(separateDuties ? [{ id: "security", label: t("auth.firstAccount.stepSecurity") }] : []),
    { id: "protect", label: t("auth.firstAccount.stepProtect") },
  ];
  const title =
    step === "admin" ? t("auth.firstAccount.adminTitle") : step === "security" ? t("auth.firstAccount.securityTitle") : t("auth.firstAccount.protectTitle");
  const description =
    step === "admin"
      ? t("auth.firstAccount.adminDescription")
      : step === "security"
        ? t("auth.firstAccount.securityDescription")
        : t("auth.firstAccount.protectDescription");

  return (
    <AuthFrame
      title={title}
      description={description}
      footer={
        <div className="flex flex-col items-start gap-4">
          {step === "admin" ? (
            <Button variant="ghost" size="sm" onClick={() => void leave()}>
              {t("auth.firstAccount.notNow")}
            </Button>
          ) : null}
          <CommandHint label={t("auth.firstAccount.fromTerminal")} command={step === "security" ? "noust user create NAME --role security" : "noust user create NAME --role admin"} />
        </div>
      }
    >
      <div className="flex flex-col gap-6">
        <Stepper orientation="horizontal" steps={steps} current={step} />
        {failedAccount !== null ? (
          <div className="flex flex-col gap-3">
            <ErrorBlock live compact error={failedAccount.error} title={t("auth.firstAccount.signInFailed", { name: failedAccount.name })} />
            {/* The access token's session is still the one open: it ends first, or the sign-in page would send it on. */}
            <Button variant="primary" size="lg" className="w-full" loading={signingOut} onClick={() => void signOut()}>
              {t("auth.firstAccount.signInAs", { name: failedAccount.name })}
            </Button>
          </div>
        ) : step === "admin" ? (
          <AccountForm
            key="admin"
            accountRole="admin"
            submitLabel={t("auth.firstAccount.createAdmin")}
            onCreated={async (account, credentials) => {
              // Known by now almost always; waited for when the account was made faster than
              // the answer, so the next step never depends on which came first.
              const state = compliance.data ?? (await queryClient.query(complianceQuery()).catch(() => undefined));
              if (splitsDuties(state)) {
                setAdmin(credentials);
                setStep("security");
              } else {
                finish(credentials, account.username);
              }
            }}
          />
        ) : step === "security" ? (
          <div className="flex flex-col gap-5">
            <Notice title={t("auth.firstAccount.whyTitle")}>{t("auth.firstAccount.why")}</Notice>
            <AccountForm
              key="security"
              accountRole="security"
              submitLabel={t("auth.firstAccount.createSecurity")}
              onCreated={() => {
                if (admin !== null) finish(admin, admin.username);
              }}
            />
            <Button
              variant="ghost"
              size="sm"
              className="self-start"
              loading={signIn.isPending}
              onClick={() => {
                if (admin !== null) finish(admin, admin.username);
              }}
            >
              {t("auth.firstAccount.skipSecurity")}
            </Button>
          </div>
        ) : session?.authenticated === true ? (
          <SecondFactor session={session} onDone={leave} optional={{ onSkip: () => void leave() }} />
        ) : null}
      </div>
    </AuthFrame>
  );
}
