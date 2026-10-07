import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { ElevationCancelledError, request } from "../../../api/client";
import { isJobFinished, jobKeys, useFollowedJob } from "../../../api/queries/jobs";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Stepper } from "../../../components/page/Stepper";
import { WizardActions } from "../../../components/page/Wizard";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { CopyTextButton } from "../../../components/ui/CopyTextButton";
import { Dialog } from "../../../components/ui/Dialog";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field } from "../../../components/ui/Field";
import { ICONS } from "../../../components/ui/icons";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { Textarea } from "../../../components/ui/Textarea";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { jobState } from "../../databases/jobs";
import { JobLogPanel } from "../../jobs/JobLogPanel";
import { Added } from "../../settings/servers/AddServerDialog";
import { fleetKeys } from "../data";
import { nodeKeys, nodesQuery } from "../nodes";
import { EMPTY_ENROLL, enrollBody, firstProblem, hostKeyBody, withoutCredential } from "./data";
import type { Access, Credential, EnrollForm, Problem } from "./data";

type Step = "server" | "hostKey" | "add";

/** The private key box, found by id to be focused: the textarea of the kit takes no ref. */
const KEY_ID = "enroll-private-key";

/** The command that prints the host key's fingerprint on the server itself: not translated. */
const FINGERPRINT_COMMAND = "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub";

export interface EnrollServerDialogProps {
  open: boolean;
  onClose: () => void;
  /** Closes this and opens the way that authorizes with a command run on the server. */
  onManual: () => void;
}

/**
 * Adding a server from an address and a credential, in the console. (1) Name it, say where it is
 * and how to get in (a password or a key, used once and kept nowhere, said so); (2) the server's
 * host key is asked for and its fingerprint shown, to be compared with the server's own before
 * anything else is sent; (3) the job: Noust installed there when it is missing, this central
 * authorized, the server joined, with the installer's output streaming. The credential is
 * emptied from the page the moment the request carrying it leaves. Confirming asks "Confirm it's
 * you".
 */
export function EnrollServerDialog({ open, onClose, onManual }: EnrollServerDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("server");
  const [form, setForm] = useState<EnrollForm>(EMPTY_ENROLL);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const followed = useFollowedJob();
  const nameRef = useRef<HTMLInputElement>(null);
  const hostRef = useRef<HTMLInputElement>(null);
  const portRef = useRef<HTMLInputElement>(null);
  const userRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const set = (patch: Partial<EnrollForm>): void => {
    setForm((previous) => ({ ...previous, ...patch }));
    setProblem(null);
  };

  const scan = useMutation({
    mutationFn: () => request("post", "/api/fleet/enroll/host-key", { body: hostKeyBody(form) }),
    onSuccess: () => {
      setConfirmed(false);
      setStep("hostKey");
    },
  });
  const enroll = useMutation({
    mutationFn: (fingerprint: string) => request("post", "/api/fleet/enroll", { body: enrollBody(form, fingerprint) }),
    onSuccess: (accepted) => {
      // The request has left: nothing of the credential stays in this page.
      setForm(withoutCredential);
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      followed.follow(accepted.job_id);
      setStep("add");
    },
    onError: (error) => {
      // Declining "Confirm it's you" changes nothing: the confirmation stays where it was.
      if (error instanceof ElevationCancelledError) enroll.reset();
    },
  });

  const job = followed.job;
  const finished = job !== null && isJobFinished(job);
  const running = followed.id !== null && !finished;
  const succeeded = job !== null && jobState(job.status) === "succeeded";
  const settled = useRef<string | null>(null);
  useEffect(() => {
    if (job === null || !isJobFinished(job) || settled.current === job.id) return;
    settled.current = job.id;
    void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
    void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
  }, [job, queryClient]);
  const nodes = useQuery({ ...nodesQuery(), enabled: succeeded });
  const node = nodes.data?.items.find((item) => item.name === form.name.trim()) ?? null;

  const reset = (): void => {
    setStep("server");
    setForm(EMPTY_ENROLL);
    setProblem(null);
    setConfirmed(false);
    followed.dismiss();
    scan.reset();
    enroll.reset();
  };
  const close = (): void => {
    if (scan.isPending || enroll.isPending) return;
    if (running) {
      toast.info(t("enroll.job.running", { name: form.name }), {
        description: t("enroll.job.backgrounded", { name: form.name }),
        action: { label: t("enroll.job.tryAgain"), onClick: () => void navigate({ to: "/activity" }) },
      });
    }
    onClose();
    reset();
  };

  const focusOf = (found: Problem): void => {
    if (found === "key" || found === "keyShape") {
      document.getElementById(KEY_ID)?.focus();
      return;
    }
    const target = { name: nameRef, host: hostRef, port: portRef, user: userRef, password: passwordRef }[found];
    target.current?.focus();
  };

  const toHostKey = (): void => {
    const found = firstProblem(form);
    setProblem(found);
    if (found !== null) {
      focusOf(found);
      return;
    }
    scan.mutate();
  };

  const problemWords = (found: Problem): string => {
    switch (found) {
      case "name":
        return t("enroll.server.nameInvalid");
      case "host":
        return t("enroll.server.hostRequired");
      case "port":
        return t("enroll.server.portInvalid");
      case "user":
        return t("enroll.server.userRequired");
      case "password":
        return t("enroll.server.passwordRequired");
      case "key":
        return t("enroll.server.keyRequired");
      case "keyShape":
        return t("enroll.server.keyInvalid");
    }
  };

  const scanned = scan.data;
  const steps = [
    { id: "server", label: t("enroll.steps.server") },
    { id: "hostKey", label: t("enroll.steps.hostKey") },
    { id: "add", label: t("enroll.steps.add") },
  ];
  const name = form.name.trim();

  let body;
  let footer;
  if (step === "server") {
    body = (
      <form
        noValidate
        className="flex min-w-0 flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          toHostKey();
        }}
      >
        <Field label={t("enroll.server.name")} description={t("enroll.server.nameHelp")} error={problem === "name" ? problemWords("name") : undefined}>
          <Input ref={nameRef} mono maxLength={32} value={form.name} onValueChange={(value: string) => set({ name: value })} placeholder="web-2" autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <div className="flex min-w-0 flex-wrap gap-4">
          <Field label={t("enroll.server.host")} description={t("enroll.server.hostHelp")} error={problem === "host" ? problemWords("host") : undefined} className="min-w-56 flex-1">
            <Input ref={hostRef} mono value={form.host} onValueChange={(value: string) => set({ host: value })} placeholder="203.0.113.9" autoComplete="off" autoCapitalize="off" spellCheck={false} />
          </Field>
          <Field label={t("enroll.server.port")} error={problem === "port" ? problemWords("port") : undefined} className="w-28">
            <Input ref={portRef} mono inputMode="numeric" value={form.port} onValueChange={(value: string) => set({ port: value })} autoComplete="off" />
          </Field>
        </div>
        <Field label={t("enroll.server.user")} description={t("enroll.server.userHelp")} error={problem === "user" ? problemWords("user") : undefined} className="max-w-xs">
          <Input ref={userRef} mono value={form.user} onValueChange={(value: string) => set({ user: value })} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <ChoiceCards<Credential>
          legend={t("enroll.server.credential")}
          value={form.credential}
          onValueChange={(credential) => set({ credential })}
          options={[
            { value: "password", label: t("enroll.server.credentialPassword"), description: t("enroll.server.credentialPasswordHelp") },
            { value: "key", label: t("enroll.server.credentialKey"), description: t("enroll.server.credentialKeyHelp") },
          ]}
        />
        {form.credential === "password" ? (
          <Field label={t("enroll.server.passwordLabel")} error={problem === "password" ? problemWords("password") : undefined} className="max-w-sm">
            <Input ref={passwordRef} mono type="password" value={form.password} onValueChange={(value: string) => set({ password: value })} autoComplete="off" spellCheck={false} />
          </Field>
        ) : (
          <>
            <Field label={t("enroll.server.keyLabel")} description={t("enroll.server.keyHelp")} error={problem === "key" || problem === "keyShape" ? problemWords(problem) : undefined}>
              <Textarea id={KEY_ID} mono rows={5} value={form.privateKey} onChange={(event) => set({ privateKey: event.target.value })} autoComplete="off" spellCheck={false} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
            </Field>
            <Field label={t("enroll.server.passphraseLabel")} description={t("enroll.server.passphraseHelp")} optional className="max-w-sm">
              <Input mono type="password" value={form.passphrase} onValueChange={(value: string) => set({ passphrase: value })} autoComplete="off" spellCheck={false} />
            </Field>
          </>
        )}
        <div className="flex items-start gap-2.5 rounded-control border border-border px-3 py-2.5">
          <ICONS.locked aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
          <div className="flex min-w-0 flex-col gap-0.5">
            <p className="text-13 font-medium text-fg">{t("enroll.server.onceTitle")}</p>
            <p className="text-13 text-pretty text-fg-muted">{t("enroll.server.onceBody")}</p>
          </div>
        </div>
        <Field label={t("enroll.server.access")} nativeLabel={false} description={t("servers.add.access.description")}>
          <Select<Access>
            value={form.access}
            onValueChange={(access) => set({ access })}
            options={[
              { value: "admin", label: t("fleet.access.level.admin"), hint: t("servers.add.access.adminHint") },
              { value: "deploy", label: t("fleet.access.level.deploy"), hint: t("servers.add.access.deployHint") },
              { value: "read", label: t("fleet.access.level.read"), hint: t("servers.add.access.readHint") },
            ]}
          />
        </Field>
        <Disclosure label={t("enroll.server.more")} defaultOpen={form.hostAccess || !form.install || form.replaceToken}>
          <div className="flex min-w-0 flex-col gap-4">
            <Checkbox checked={form.hostAccess} onCheckedChange={(hostAccess) => set({ hostAccess })} label={t("enroll.server.hostAccess")} description={t("enroll.server.hostAccessHelp")} />
            <Checkbox checked={form.install} onCheckedChange={(install) => set({ install })} label={t("enroll.server.install")} description={t("enroll.server.installHelp")} />
            <Checkbox checked={form.replaceToken} onCheckedChange={(replaceToken) => set({ replaceToken })} label={t("enroll.server.replaceToken")} description={t("enroll.server.replaceTokenHelp")} />
          </div>
        </Disclosure>
        <div className="flex min-w-0 flex-col items-start gap-3">
          <Button variant="ghost" size="sm" onClick={onManual}>
            {t("enroll.manualInstead")}
          </Button>
          <CommandHint command="noust fleet enroll web-2 --host 203.0.113.9" label={t("databases.common.fromTerminal")} />
        </div>
        {scan.isError ? <ErrorBlock live compact error={scan.error} title={t("enroll.hostKey.scanFailed")} /> : null}
      </form>
    );
    footer = (
      <WizardActions
        className="w-full"
        back={{ label: t("enroll.cancel"), onClick: close }}
        next={{ label: t("enroll.next"), loading: scan.isPending, onClick: toHostKey }}
      />
    );
  } else if (step === "hostKey" && scanned !== undefined) {
    body = (
      <div className="flex min-w-0 flex-col gap-5">
        <div className="flex flex-col gap-1">
          <p className="title text-16 text-fg">{t("enroll.hostKey.title")}</p>
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("enroll.hostKey.intro", { host: scanned.host })}</p>
        </div>
        <KeyValueList
          items={[
            { label: t("enroll.hostKey.fingerprint"), value: scanned.fingerprint },
            { label: t("enroll.hostKey.keyType"), value: scanned.key_type },
          ]}
        />
        <div className="flex min-w-0 flex-col gap-2">
          <p className="text-13 text-fg-muted">{t("enroll.hostKey.howTo")}</p>
          <code translate="no" className="rounded-control border border-border-strong bg-bg-sunken px-3 py-2 text-12 break-all text-fg select-all">
            {FINGERPRINT_COMMAND}
          </code>
          <div>
            <CopyTextButton value={FINGERPRINT_COMMAND} variant="secondary">
              {t("servers.add.authorize.copy")}
            </CopyTextButton>
          </div>
        </div>
        <Checkbox checked={confirmed} onCheckedChange={setConfirmed} label={t("enroll.hostKey.confirm")} description={t("enroll.hostKey.confirmHelp")} />
        {enroll.isError && !(enroll.error instanceof ElevationCancelledError) ? <ErrorBlock live compact error={enroll.error} title={t("enroll.job.startFailed")} /> : null}
      </div>
    );
    footer = (
      <WizardActions
        className="w-full"
        back={{
          label: t("enroll.back"),
          onClick: () => {
            if (!enroll.isPending) setStep("server");
          },
        }}
        next={{ label: t("enroll.submit"), loading: enroll.isPending, onClick: () => enroll.mutate(scanned.fingerprint) }}
        {...(!confirmed ? { missing: t("enroll.hostKey.required") } : {})}
      />
    );
  } else {
    const state = job === null ? "queued" : jobState(job.status);
    body = (
      <div className="flex min-w-0 flex-col gap-4">
        <JobLogPanel
          job={job}
          words={{ queued: t("enroll.job.queued", { name }), running: t("enroll.job.running", { name }), succeeded: t("enroll.job.succeeded", { name }), failed: t("enroll.job.failed", { name }) }}
          label={t("enroll.job.logLabel", { name })}
          height={260}
          result={node !== null ? <Added t={t} node={node} onNavigate={close} /> : null}
        />
        <p className="text-12 text-fg-faint">{t("enroll.job.cleared")}</p>
      </div>
    );
    footer = (
      <>
        {state === "failed" ? (
          <Button
            icon={<ICONS.dismiss aria-hidden="true" />}
            onClick={() => {
              followed.dismiss();
              enroll.reset();
              setStep("server");
            }}
          >
            {t("enroll.job.tryAgain")}
          </Button>
        ) : null}
        <Button variant="primary" onClick={close}>
          {finished ? t("enroll.done") : t("enroll.close")}
        </Button>
      </>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      size="lg"
      initialFocus={nameRef}
      title={t("enroll.title")}
      description={t("enroll.description")}
      footer={footer}
    >
      <div className="flex min-w-0 flex-col gap-5">
        <Stepper orientation="horizontal" steps={steps} current={step} />
        {body}
      </div>
    </Dialog>
  );
}
