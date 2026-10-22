import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { Fragment, useId, useRef, useState } from "react";
import type { KeyboardEvent, SyntheticEvent } from "react";

import { ElevationCancelledError, isApiError } from "../../../api/client";
import { sessionQuery } from "../../../api/queries/auth";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Stepper } from "../../../components/page/Stepper";
import { WizardActions } from "../../../components/page/Wizard";
import { Button } from "../../../components/ui/Button";
import { CopyValue } from "../../../components/ui/CopyValue";
import { Dialog } from "../../../components/ui/Dialog";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field } from "../../../components/ui/Field";
import { ICONS } from "../../../components/ui/icons";
import { Input } from "../../../components/ui/Input";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { fleetKeys } from "../../fleet/data";
import { acceptServer, cancelInvite, classifyLinkCode, inviteServer, linkKeys, linkStatusQuery } from "../../fleet/link";
import type { Invite, LinkCodeProblem, LinkCodeSummary } from "../../fleet/link";
import { NODE_NAME, nodeKeys } from "../../fleet/nodes";
import type { NodeRecord } from "../../fleet/nodes";
import { Added, Failed } from "./AddServerDialog";

type Step = "invite" | "code" | "result";

/** What a link code looks like, for the empty field: its prefix, never a real one. */
const LINK_PLACEHOLDER = "noust-link:v1:…";

/** A host name or an address, as the server will dial it (an IPv6 address may be in brackets). */
const HOST = /^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?|\[?[0-9a-fA-F:.]+\]?)$/;

const PROBLEM_WORDS: Readonly<Record<LinkCodeProblem, `servers.link.code.problem.${LinkCodeProblem}`>> = {
  empty: "servers.link.code.problem.empty",
  joinCode: "servers.link.code.problem.joinCode",
  apiToken: "servers.link.code.problem.apiToken",
  consoleToken: "servers.link.code.problem.consoleToken",
  newer: "servers.link.code.problem.newer",
  wrongPrefix: "servers.link.code.problem.wrongPrefix",
  unreadable: "servers.link.code.problem.unreadable",
};

/** The address to start from: the one this console was reached at, unless it is this machine's own. */
export function defaultCentralHost(hostname: string): string {
  const host = hostname.trim();
  return host === "" || host === "localhost" || host.startsWith("127.") || host === "::1" || host === "[::1]" ? "" : host;
}

function validPort(text: string, least: number): boolean {
  return /^\d+$/.test(text) && Number(text) >= least && Number(text) <= 65535;
}

/** The command to run on the server, whole and wrapped: a key cut off cannot be checked by eye. */
function LinkCommand({ t, name, command }: { t: T; name: string; command: string }) {
  const labelId = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <span id={labelId} className="text-13 font-medium text-fg">
        {t("servers.link.command.label", { name })}
      </span>
      <div data-testid="link-command">
        <CopyValue value={command} label={t("servers.link.command.copy")} name={t("servers.link.command.label", { name })} />
      </div>
    </div>
  );
}

/** What the pasted code says, without its token, and whether it answers this invitation. */
function CodeSummary({ t, summary, invite }: { t: T; summary: LinkCodeSummary; invite: Invite | null }) {
  const wantedNode = invite?.node ?? summary.node;
  const wantedCentral = invite?.central ?? summary.central;
  const otherNode = summary.node !== wantedNode;
  const otherCentral = summary.central !== wantedCentral;
  return (
    <div className="flex flex-col gap-2">
      <InlineMessage tone={otherNode || otherCentral ? "warning" : "success"}>
        {t.rich("servers.link.code.summary", {
          node: <Mono key="node">{summary.node}</Mono>,
          console: <Mono key="console">{String(summary.consolePort)}</Mono>,
          version: <Mono key="version">{summary.version}</Mono>,
        })}
      </InlineMessage>
      {otherNode ? <InlineMessage>{t("servers.link.code.otherNode", { node: summary.node, name: wantedNode })}</InlineMessage> : null}
      {otherCentral ? <InlineMessage>{t("servers.link.code.otherCentral", { central: summary.central, name: wantedCentral })}</InlineMessage> : null}
    </div>
  );
}

/** A refusal about the code itself (malformed, for another invitation): the code is what to change. */
function isCodeError(error: unknown): boolean {
  if (!isApiError(error)) return false;
  return error.fields?.["link_code"] !== undefined || error.extra["field"] === "link_code";
}

export interface LinkServerDialogProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Adding a server that this central cannot reach, because it is behind NAT or a carrier's CGNAT
 * and has no SSH port anyone can open: it dials this central instead. (1) Name it and say what
 * address it dials; this central gives it a port of its own and the command to run, as root, on
 * the OTHER server, with this central's SSH host key in it, which the server pins; (2) paste the
 * link code it printed, which this central checks against that invitation; (3) the central
 * installs the key (it may listen on its one port and nothing else), waits for the server to
 * connect and checks its token, and says how that went.
 *
 * Each step's footer is its own set of buttons (keyed by step), as in the dialog for a server the
 * central can reach.
 */
export function LinkServerDialog({ open, onClose }: LinkServerDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const { data: session } = useQuery(sessionQuery());
  const status = useQuery({ ...linkStatusQuery(), enabled: open });
  const [step, setStep] = useState<Step>("invite");
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [host, setHost] = useState(() => defaultCentralHost(typeof window === "undefined" ? "" : window.location.hostname));
  const [hostError, setHostError] = useState<string | null>(null);
  const [sshPort, setSshPort] = useState("");
  const [linkPort, setLinkPort] = useState("");
  const [portError, setPortError] = useState<string | null>(null);
  const [invite, setInvite] = useState<Invite | null>(null);
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const [added, setAdded] = useState<NodeRecord | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const hostRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const formId = useId();

  const reading = code.trim() === "" ? null : classifyLinkCode(code);
  const ready = status.data?.ready === true;

  const invitation = useMutation({
    mutationFn: () =>
      inviteServer({
        name: name.trim(),
        host: host.trim(),
        ...(sshPort.trim() !== "" ? { ssh_port: Number(sshPort) } : {}),
        ...(linkPort.trim() !== "" ? { port: Number(linkPort) } : {}),
      }),
    onSuccess: (answer) => {
      setInvite(answer);
      void queryClient.invalidateQueries({ queryKey: linkKeys.all });
    },
    onError: (error) => {
      // Declining "Confirm it's you" is not a refusal: the form stays as it was.
      if (error instanceof ElevationCancelledError) return;
    },
  });

  // The invitation a changed form no longer matches is given back, so its port is free again.
  const cancel = useMutation({ mutationFn: (previous: string) => cancelInvite(previous) });

  const accept = useMutation({
    mutationFn: (linkCode: string) => acceptServer({ name: invite?.node ?? name.trim(), link_code: linkCode }),
    onSuccess: (node) => {
      // The code carries a token: it is not kept a moment longer than it is needed.
      setCode("");
      setAdded(node);
      setFailure(null);
      setStep("result");
      toast.success(t("servers.link.addedToast", { name: node.name }));
      void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
      void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
      void queryClient.invalidateQueries({ queryKey: linkKeys.all });
    },
    onError: (error) => {
      if (error instanceof ElevationCancelledError) return;
      if (isCodeError(error)) setCode("");
      setFailure(error);
      setStep("result");
    },
  });

  const pending = invitation.isPending || accept.isPending;

  const reset = (): void => {
    setStep("invite");
    setName("");
    setNameError(null);
    setHostError(null);
    setSshPort("");
    setLinkPort("");
    setPortError(null);
    setInvite(null);
    setCode("");
    setCodeError(null);
    setAdded(null);
    setFailure(null);
    invitation.reset();
    cancel.reset();
    accept.reset();
  };

  const onOpenChange = (next: boolean): void => {
    // Pending includes "Confirm it's you", which opens over this dialog.
    if (next || pending) return;
    onClose();
    reset();
  };
  const close = (): void => {
    onOpenChange(false);
  };

  /** What the invitation was for changed: the command shown is not this form's any more. */
  const staleInvite = (): void => {
    if (invite === null) return;
    cancel.mutate(invite.node);
    setInvite(null);
  };

  const submitInvite = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (pending || !ready) return;
    const nameOk = NODE_NAME.test(name.trim());
    const hostOk = HOST.test(host.trim());
    const portsOk = (sshPort.trim() === "" || validPort(sshPort.trim(), 1)) && (linkPort.trim() === "" || validPort(linkPort.trim(), 1024));
    setNameError(nameOk ? null : t("servers.add.name.invalid"));
    setHostError(hostOk ? null : t("servers.link.host.invalid"));
    setPortError(portsOk ? null : t("servers.link.ports.invalid"));
    if (!nameOk) {
      nameRef.current?.focus();
      return;
    }
    if (!hostOk) {
      hostRef.current?.focus();
      return;
    }
    if (!portsOk) return;
    invitation.mutate();
  };

  const submitCode = (): void => {
    if (pending) return;
    const read = classifyLinkCode(code);
    setCodeError(read.ok ? null : t(PROBLEM_WORDS[read.problem]));
    if (!read.ok) {
      codeRef.current?.focus();
      return;
    }
    accept.mutate(read.code);
  };

  const onEnter = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    submitCode();
  };

  const serverName = invite?.node ?? name.trim();
  const twoFactorOff = session !== undefined && !session.totp_enabled;

  let body;
  let footer;
  if (step === "invite") {
    body = (
      <div className="flex flex-col gap-5">
        <Notice tone="warning" title={t("servers.link.command.whereTitle")}>
          {t("servers.link.command.where", { central: session?.hostname ?? "" })}
        </Notice>
        {status.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : status.isError ? (
          <ErrorBlock compact error={status.error} title={t("servers.link.status.failed")} onRetry={() => void status.refetch()} />
        ) : !ready ? (
          <Notice tone="warning" title={t("servers.link.notReady.title")}>
            <div className="flex flex-col gap-2">
              <ul aria-label={t("servers.link.notReady.listLabel")} className="flex list-disc flex-col gap-1 pl-5">
                {(status.data.problems ?? []).map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
              <CommandHint command={status.data.setup_command} label={t("servers.link.notReady.run")} />
            </div>
          </Notice>
        ) : null}
        <form id={formId} noValidate onSubmit={submitInvite} className="flex flex-col gap-4">
          <Field label={t("servers.add.name.label")} description={t("servers.add.name.description")} error={nameError}>
            <Input
              ref={nameRef}
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              maxLength={32}
              placeholder="web-5"
              value={name}
              disabled={invitation.isPending}
              onValueChange={(value: string) => {
                setName(value);
                if (invite !== null && value.trim() !== invite.node) staleInvite();
              }}
            />
          </Field>
          <Field
            label={t("servers.link.host.label")}
            description={t("servers.link.host.description")}
            error={hostError}
            action={
              // design-exception: submit-disabled-invalid the central cannot make an invitation until its own setup is done, which the notice above lists with the command to run
              <Button type="submit" loading={invitation.isPending} disabled={!ready}>
                {invite === null ? t("servers.link.showCommand") : t("servers.link.again")}
              </Button>
            }
          >
            <Input
              ref={hostRef}
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              placeholder="nas.example.com"
              value={host}
              disabled={invitation.isPending}
              onValueChange={(value: string) => {
                setHost(value);
                if (invite !== null && value.trim() !== invite.host) staleInvite();
              }}
            />
          </Field>
          <Disclosure label={t("servers.link.ports.label")} defaultOpen={sshPort !== "" || linkPort !== ""}>
            <div className="flex min-w-0 flex-col gap-4">
              <Field label={t("servers.link.ports.ssh")} description={t("servers.link.ports.sshHelp", { port: String(status.data?.ssh_port ?? 22) })} error={portError}>
                <Input mono inputMode="numeric" className="w-32" value={sshPort} disabled={invitation.isPending} onValueChange={(value: string) => setSshPort(value)} autoComplete="off" />
              </Field>
              <Field label={t("servers.link.ports.link")} description={t("servers.link.ports.linkHelp")}>
                <Input mono inputMode="numeric" className="w-32" value={linkPort} disabled={invitation.isPending} onValueChange={(value: string) => setLinkPort(value)} autoComplete="off" />
              </Field>
            </div>
          </Disclosure>
        </form>
        {invitation.isError && !(invitation.error instanceof ElevationCancelledError) ? <ErrorBlock live compact error={invitation.error} title={t("servers.link.inviteFailed")} /> : null}
        {invite !== null ? (
          <div className="flex flex-col gap-4">
            <p className="text-14 text-pretty text-fg">{t("servers.link.command.intro", { name: invite.node })}</p>
            <LinkCommand t={t} name={invite.node} command={invite.command} />
            <p className="text-13 text-pretty text-fg-muted">
              {t.rich("servers.link.command.hostKey", {
                name: invite.node,
                fingerprint: <Mono key="fingerprint">{invite.host_key_fingerprint}</Mono>,
              })}
            </p>
            <p className="text-13 text-fg-muted">{t.rich("servers.link.command.expires", { when: <RelativeTime key="when" value={invite.expires_at} /> })}</p>
            <div className="flex items-start gap-2.5 rounded-control border border-border px-3 py-2.5">
              <ICONS.locked aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
              <div className="flex min-w-0 flex-col gap-0.5">
                <p className="text-13 font-medium text-fg">{t("servers.link.command.safeTitle")}</p>
                <p className="text-13 text-pretty text-fg-muted">{t("servers.link.command.safeBody", { name: invite.node })}</p>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    );
    footer = (
      <Fragment key="invite">
        <WizardActions
          className="w-full"
          back={{ label: t("servers.add.cancel"), onClick: close }}
          next={{
            label: t("servers.add.next"),
            onClick: () => {
              setStep("code");
              requestAnimationFrame(() => codeRef.current?.focus());
            },
          }}
          {...(invite === null ? { missing: t("servers.link.command.missing"), onMissing: () => nameRef.current?.focus() } : {})}
        />
      </Fragment>
    );
  } else if (step === "code") {
    body = (
      <div className="flex flex-col gap-5">
        <p className="text-14 text-pretty text-fg">{t("servers.link.code.intro", { name: serverName })}</p>
        <Field label={t("servers.link.code.label")} description={t("servers.link.code.description", { name: serverName })} error={codeError}>
          <Input
            ref={codeRef}
            type="password"
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder={LINK_PLACEHOLDER}
            value={code}
            disabled={accept.isPending}
            onKeyDown={onEnter}
            onValueChange={(value: string) => {
              setCode(value);
              if (codeError !== null) setCodeError(null);
            }}
          />
        </Field>
        {reading !== null && !reading.ok && reading.problem !== "empty" && codeError === null ? <InlineMessage>{t(PROBLEM_WORDS[reading.problem])}</InlineMessage> : null}
        {reading?.ok === true ? <CodeSummary t={t} summary={reading.summary} invite={invite} /> : null}
        {accept.isPending ? (
          <InlineMessage tone="info" live>
            {t("servers.link.code.waiting", { name: serverName })}
          </InlineMessage>
        ) : null}
        <p className="text-12 text-fg-muted">{t("servers.link.code.elevationNote")}</p>
      </div>
    );
    footer = (
      <Fragment key="code">
        <WizardActions
          className="w-full"
          back={{
            label: t("servers.add.back"),
            onClick: () => {
              if (!accept.isPending) setStep("invite");
            },
          }}
          next={{ label: t("servers.link.code.submit"), loading: accept.isPending, onClick: submitCode }}
        />
      </Fragment>
    );
  } else {
    body =
      added !== null ? <Added t={t} node={added} onNavigate={close} /> : <Failed t={t} name={serverName} error={failure} twoFactorOff={twoFactorOff} onNavigate={close} />;
    footer =
      added !== null ? (
        <Fragment key="done">
          <Button variant="primary" onClick={close}>
            {t("servers.add.done")}
          </Button>
        </Fragment>
      ) : (
        <Fragment key="try-again">
          <Button onClick={close}>{t("servers.add.cancel")}</Button>
          <Button
            type="button"
            variant="primary"
            icon={<ArrowLeft aria-hidden="true" />}
            onClick={(event) => {
              // Only goes back: this click must never reach a form and send the code again.
              event.preventDefault();
              setFailure(null);
              accept.reset();
              setStep("code");
              requestAnimationFrame(() => codeRef.current?.focus());
            }}
          >
            {t("servers.link.result.tryAgain")}
          </Button>
        </Fragment>
      );
  }

  const steps = [
    { id: "invite", label: t("servers.link.steps.invite") },
    { id: "code", label: t("servers.link.steps.code") },
    { id: "result", label: t("servers.link.steps.result") },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="lg" initialFocus={nameRef} title={t("servers.link.title")} description={t("servers.link.description")} footer={footer}>
      <div className="flex flex-col gap-5">
        <Stepper orientation="horizontal" steps={steps} current={step} />
        {body}
      </div>
    </Dialog>
  );
}
