import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { KeyRound, LogOut, Smartphone } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { authKeys, confirmTwoFactor, enrollTwoFactor, sessionQuery } from "../../api/queries/auth";
import type { SessionInfo, TwoFactorEnrollment } from "../../api/queries/auth";
import { useDocumentTitle } from "../../app/documentTitle";
import { nodeOfConsolePath } from "../../app/nodeRoute";
import { ErrorBlock } from "../../components/page/QueryState";
import { Stepper } from "../../components/page/Stepper";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { splitErrors } from "../settings/formErrors";
import { BackupCodesPanel } from "./BackupCodes";
import { AuthFrame } from "./AuthFrame";
import { registerPasskey } from "./passkeys";
import { NoticeText } from "./NoticeText";
import { TotpSetup } from "./TotpSetup";
import { useSignOut } from "./useSignOut";
import { suggestedName } from "./passkeyName";
import { CeremonyError, browserLimit } from "./webauthn";

type Step = "factor" | "notice";

/** The checks this session still has, in order. */
export function checksOf(session: SessionInfo | undefined): Step[] {
  if (session === undefined) return [];
  return [...(session.mfa_required ? (["factor"] as const) : []), ...(session.notice ? (["notice"] as const) : [])];
}

// ---------------------------------------------------------------------------------------
// The second factor

type FactorStage = { stage: "choose" } | { stage: "app"; enrollment: TwoFactorEnrollment } | { stage: "passkey" } | { stage: "codes"; codes: readonly string[] };

function SecondFactor({ session, onDone }: { session: SessionInfo; onDone: () => Promise<void> }) {
  const t = useT();
  const [state, setState] = useState<FactorStage>({ stage: "choose" });
  const [code, setCode] = useState("");
  const [name, setName] = useState(() => suggestedName(t));
  const [saved, setSaved] = useState(false);
  const [nudge, setNudge] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);
  const formId = useId();
  const hostname = session.hostname;
  const passkeysHere = browserLimit() === null;

  const start = useMutation({
    mutationFn: enrollTwoFactor,
    onSuccess: (enrollment) => {
      setState({ stage: "app", enrollment });
      setCode("");
    },
  });
  const confirm = useMutation({
    mutationFn: (value: string) => confirmTwoFactor(value),
    onSuccess: (result) => {
      setState({ stage: "codes", codes: result.backup_codes });
    },
    onError: () => {
      codeRef.current?.select();
    },
  });
  const passkey = useMutation({
    mutationFn: () => registerPasskey(name.trim()),
    onSuccess: async (result) => {
      if (result.backup_codes && result.backup_codes.length > 0) setState({ stage: "codes", codes: result.backup_codes });
      else await onDone();
    },
  });
  const codeError = splitErrors(confirm.error, ["code"], "code");

  if (state.stage === "codes") {
    return (
      <Card title={t("settings.security.twoFactor.backupCodes.title")} description={t("auth.gate.codesDescription")} level={2}>
        <div className="flex flex-col gap-5">
          <BackupCodesPanel
            codes={state.codes}
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
              void onDone();
            }}
          >
            {t("auth.gate.continue")}
          </Button>
        </div>
      </Card>
    );
  }

  if (state.stage === "app") {
    const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
      event.preventDefault();
      const value = code.replace(/\s+/g, "");
      if (value === "" || confirm.isPending) return;
      confirm.mutate(value);
    };
    return (
      <div className="flex flex-col gap-5">
        <TotpSetup layout="narrow" uri={state.enrollment.uri} secret={state.enrollment.secret}>
          <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-4">
            <Field label={t("settings.security.twoFactor.enroll.codeLabel")} error={codeError.fields.code}>
              <Input
                ref={codeRef}
                mono
                inputMode="numeric"
                autoComplete="one-time-code"
                spellCheck={false}
                maxLength={8}
                value={code}
                onValueChange={(value: string) => {
                  setCode(value);
                  if (confirm.isError) confirm.reset();
                }}
                className="w-40"
              />
            </Field>
            {codeError.form !== null ? <ErrorBlock live compact error={codeError.form} title={t("settings.security.twoFactor.enroll.codeErrorTitle")} /> : null}
            <Button type="submit" variant="primary" size="lg" className="w-full" loading={confirm.isPending}>
              {t("auth.gate.turnOn")}
            </Button>
          </form>
        </TotpSetup>
        <Button variant="ghost" size="sm" className="self-start" onClick={() => setState({ stage: "choose" })}>
          {t("auth.gate.otherWay")}
        </Button>
      </div>
    );
  }

  if (state.stage === "passkey") {
    return (
      <form
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim() !== "" && !passkey.isPending) passkey.mutate();
        }}
      >
        <Field label={t("auth.passkeys.nameLabel")} description={t("auth.passkeys.nameHint")}>
          <Input
            value={name}
            maxLength={64}
            onValueChange={(value: string) => {
              setName(value);
            }}
          />
        </Field>
        {passkey.isError && !(passkey.error instanceof CeremonyError && passkey.error.cancelled) ? (
          <ErrorBlock live compact error={passkey.error} title={t("auth.passkeys.addFailed")} />
        ) : null}
        <Button type="submit" variant="primary" size="lg" className="w-full" icon={<KeyRound aria-hidden="true" />} loading={passkey.isPending}>
          {t("auth.passkeys.create")}
        </Button>
        <Button variant="ghost" size="sm" className="self-start" onClick={() => setState({ stage: "choose" })}>
          {t("auth.gate.otherWay")}
        </Button>
      </form>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {start.isError ? <ErrorBlock live compact error={start.error} title={t("settings.security.twoFactor.startFailed")} /> : null}
      <Button variant="primary" size="lg" className="w-full" icon={<Smartphone aria-hidden="true" />} loading={start.isPending} onClick={() => start.mutate()}>
        {t("auth.gate.useApp")}
      </Button>
      {passkeysHere ? (
        <Button size="lg" className="w-full" icon={<KeyRound aria-hidden="true" />} onClick={() => setState({ stage: "passkey" })}>
          {t("auth.gate.usePasskey")}
        </Button>
      ) : (
        <p className="text-12 text-fg-muted">{t("auth.gate.passkeyUnavailable")}</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// The usage notice

function UsageNotice({ notice, onDone }: { notice: { text: string; version: string }; onDone: () => Promise<void> }) {
  const t = useT();
  const [accepted, setAccepted] = useState(false);
  const [missing, setMissing] = useState(false);
  const accept = useMutation({
    mutationFn: () => request("post", "/api/auth/notice/accept", { body: { version: notice.version } }),
    onSuccess: onDone,
  });
  return (
    <form
      noValidate
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (!accepted) {
          setMissing(true);
          return;
        }
        accept.mutate();
      }}
    >
      <NoticeText text={notice.text} label={t("auth.gate.noticeLabel")} />
      <Checkbox
        label={t("auth.gate.noticeCheckbox")}
        checked={accepted}
        onCheckedChange={(next) => {
          setAccepted(next);
          if (next) setMissing(false);
        }}
      />
      {missing ? (
        <Notice tone="warning" live>
          {t("auth.gate.noticeMissing")}
        </Notice>
      ) : null}
      {accept.isError ? <ErrorBlock live compact error={accept.error} title={t("auth.gate.noticeFailed")} /> : null}
      <Button type="submit" variant="primary" size="lg" className="w-full" loading={accept.isPending}>
        {t("auth.gate.accept")}
      </Button>
    </form>
  );
}

// ---------------------------------------------------------------------------------------

export interface GatePageProps {
  next: string;
}

/**
 * What an account does before the console opens (T7): enrol a second factor (an authenticator
 * app or a passkey), then read and accept the usage notice. The server refuses everything
 * else until both are done; each step says why it is here.
 */
export function GatePage({ next }: GatePageProps) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: session } = useQuery(sessionQuery());
  const { signOut, pending: signingOut } = useSignOut();
  // The steps as they were on arrival: done ones stay in the stepper as done.
  const [steps] = useState<Step[]>(() => checksOf(session));
  const remaining = checksOf(session);
  const current = remaining[0] ?? steps.at(-1) ?? "factor";
  useDocumentTitle(current === "factor" ? t("auth.gate.factorTitle") : t("auth.gate.noticeTitle"));

  const advance = async (): Promise<void> => {
    const fresh = await queryClient.query({ ...sessionQuery(), staleTime: 0 });
    void queryClient.invalidateQueries({ queryKey: authKeys.all });
    if (checksOf(fresh).length > 0) return;
    const { node, pathname } = nodeOfConsolePath(next);
    await navigate(node === null ? { href: next, replace: true } : { to: pathname, search: { node }, replace: true });
  };

  const labels: Record<Step, string> = { factor: t("auth.gate.stepFactor"), notice: t("auth.gate.stepNotice") };
  return (
    <AuthFrame
      title={current === "factor" ? t("auth.gate.factorTitle") : t("auth.gate.noticeTitle")}
      description={current === "factor" ? t("auth.gate.factorDescription") : t("auth.gate.noticeDescription")}
      footer={
        <Button variant="ghost" size="sm" icon={<LogOut aria-hidden="true" />} loading={signingOut} onClick={() => void signOut()}>
          {t("auth.gate.signOut")}
        </Button>
      }
    >
      <div className="flex flex-col gap-6">
        {steps.length > 1 ? <Stepper orientation="horizontal" steps={steps.map((id) => ({ id, label: labels[id] }))} current={current} /> : null}
        {session === undefined ? null : current === "factor" && session.mfa_required ? (
          <SecondFactor session={session} onDone={advance} />
        ) : session.notice ? (
          <UsageNotice notice={session.notice} onDone={advance} />
        ) : null}
      </div>
    </AuthFrame>
  );
}
