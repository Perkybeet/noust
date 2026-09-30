import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { Fragment, useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent, SyntheticEvent } from "react";

import { ElevationCancelledError, isApiError } from "../../../api/client";
import { sessionQuery } from "../../../api/queries/auth";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Stepper } from "../../../components/page/Stepper";
import { WizardActions } from "../../../components/page/Wizard";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { CopyTextButton } from "../../../components/ui/CopyTextButton";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { ICONS } from "../../../components/ui/icons";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { accessOf, fleetKeys, refreshFleetView } from "../../fleet/data";
import { ServerLink } from "../../fleet/links";
import { NODE_NAME, addNode, centralNameFrom, fetchNodeKey, nodeKeys, nodeStatus, sshAddress } from "../../fleet/nodes";
import type { NodeKey, NodeRecord } from "../../fleet/nodes";
import { ReachabilityPill } from "../../fleet/ReachabilityPill";
import { classifyJoinCode, sshFingerprint } from "./joinCode";
import type { JoinCodeProblem, JoinCodeSummary } from "./joinCode";

/** `host`, `user@host` or `user@host:port`; an IPv6 host goes in brackets. */
const SSH_TARGET = /^(?:[^@\s:[\]]+@)?(?:[^@\s:[\]]+|\[[0-9a-fA-F:.]+\])(?::\d{1,5})?$/;

type Step = "authorize" | "join" | "result";

/** What a join code looks like, for the empty field: its prefix, never a real one. */
const JOIN_PLACEHOLDER = "noust-join:v1:…";
type Access = "admin" | "deploy" | "read";

const PROBLEM_WORDS: Readonly<Record<JoinCodeProblem, `servers.add.join.problem.${JoinCodeProblem}`>> = {
  empty: "servers.add.join.problem.empty",
  apiToken: "servers.add.join.problem.apiToken",
  consoleToken: "servers.add.join.problem.consoleToken",
  newer: "servers.add.join.problem.newer",
  wrongPrefix: "servers.add.join.problem.wrongPrefix",
  unreadable: "servers.add.join.problem.unreadable",
};

/** The command, with the ceiling chosen for this central when it is not the default. */
export function authorizeCommand(command: string, access: Access): string {
  return access === "admin" ? command : `${command} --access ${access}`;
}

/** The command to run on the server, whole and wrapped: a key cut off cannot be checked by eye. */
function AuthorizeCommand({ t, name, command }: { t: T; name: string; command: string }) {
  const labelId = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <span id={labelId} className="text-13 font-medium text-fg">
        {t("servers.add.authorize.commandLabel", { name })}
      </span>
      <code
        aria-labelledby={labelId}
        translate="no"
        data-testid="authorize-command"
        className="rounded-control border border-border-strong bg-bg-sunken px-3 py-2 text-12 break-all text-fg select-all"
      >
        {command}
      </code>
      <div>
        <CopyTextButton value={command} variant="secondary">
          {t("servers.add.authorize.copy")}
        </CopyTextButton>
      </div>
    </div>
  );
}

/** What the pasted code says, without its token, and whether it was made for this central. */
function CodeSummary({ t, summary, centralName, fingerprintMatches }: { t: T; summary: JoinCodeSummary; centralName: string | null; fingerprintMatches: boolean | null }) {
  const otherCentral = summary.central !== null && centralName !== null && summary.central !== centralName;
  return (
    <div className="flex flex-col gap-2">
      <Notice tone={otherCentral || fingerprintMatches === false ? "warning" : "success"} title={t("servers.add.join.summaryTitle")}>
        {t.rich("servers.add.join.summary", {
          node: <Mono key="node">{summary.node ?? "?"}</Mono>,
          user: <Mono key="user">{summary.sshUser}</Mono>,
          port: <Mono key="port">{String(summary.sshPort)}</Mono>,
          console: <Mono key="console">{String(summary.consolePort)}</Mono>,
          version: <Mono key="version">{summary.version}</Mono>,
        })}
      </Notice>
      {otherCentral ? (
        <Notice tone="warning">{t("servers.add.join.otherCentral", { central: summary.central ?? "", name: centralName })}</Notice>
      ) : null}
      {fingerprintMatches === false ? <Notice tone="warning">{t("servers.add.join.otherKey")}</Notice> : null}
      {summary.sshUser === "root" ? <Notice>{t("servers.add.join.rootAccount")}</Notice> : null}
    </div>
  );
}

function Added({ t, node, onNavigate }: { t: T; node: NodeRecord; onNavigate: () => void }) {
  // The ceiling the server published to this central, read once it is part of the fleet.
  const access = useQuery({
    queryKey: [...fleetKeys.view("servers"), "added", node.name],
    queryFn: () => refreshFleetView("servers"),
    select: (view) => {
      const row = view.items.find((item) => item["node"] === node.name);
      return row === undefined ? null : accessOf(row);
    },
    retry: false,
  });
  return (
    <div className="flex flex-col gap-4" role="status">
      <div className="flex flex-col gap-1">
        <p className="title text-16 text-fg">{t("servers.add.result.addedTitle", { name: node.name })}</p>
        <p className="text-13 text-pretty text-fg-muted">{t("servers.add.result.addedBody")}</p>
      </div>
      <KeyValueList
        items={[
          { label: t("servers.add.result.status"), value: <ReachabilityPill reachability={nodeStatus(node)} />, copy: false, mono: false },
          {
            label: t("servers.add.result.version"),
            value: node.version ? <Mono>{node.version}</Mono> : t("servers.add.result.notReported"),
            copy: node.version ?? false,
            mono: node.version !== null && node.version !== undefined,
          },
          { label: t("servers.add.result.tunnel"), value: <Mono>{sshAddress(node)}</Mono>, copy: sshAddress(node) },
          {
            label: t("servers.add.result.access"),
            value:
              access.data === undefined || access.data === null
                ? t("servers.add.result.accessUnknown")
                : `${t(`fleet.access.level.${access.data.level}`)} · ${access.data.hostAccess ? t("fleet.access.hostAccessOn") : t("fleet.access.hostAccessOff")}`,
            copy: false,
            mono: false,
          },
        ]}
      />
      <div>
        <ServerLink node={node.name} path="/" onClick={onNavigate} className={buttonClassName("secondary")}>
          {t("servers.add.result.open", { name: node.name })}
        </ServerLink>
      </div>
    </div>
  );
}

function Failed({ t, name, error, twoFactorOff, onNavigate }: { t: T; name: string; error: unknown; twoFactorOff: boolean; onNavigate: () => void }) {
  // A registration the policy refused (two-factor still off) is fixed in Security settings.
  const policy = isApiError(error) && error.status === 400;
  return (
    <div className="flex flex-col gap-3">
      <ErrorBlock live error={error} title={t("servers.add.result.failedTitle", { name })} />
      {policy && twoFactorOff ? (
        <div>
          <Link to="/settings/security" onClick={onNavigate} className={buttonClassName("secondary")}>
            {t("servers.add.result.twoFactorLink")}
          </Link>
        </div>
      ) : null}
    </div>
  );
}

/** A refusal about the code itself (malformed, for another key): the code is what to change. */
function isCodeError(error: unknown): boolean {
  if (!isApiError(error)) return false;
  return error.fields?.["join_code"] !== undefined || error.extra["field"] === "join_code";
}

export interface AddServerDialogProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Adding a server, the inverted way: this central never logs in to it. (1) Name it and run the
 * command shown, as root, on the OTHER server - the one being added: it authorizes a key that
 * can only forward that server's console port, for a tunnel account with no shell; (2) paste
 * the join code it printed and where its SSH answers; (3) the central pins its host key, opens
 * the tunnel and checks the token, and says how that went and what the server lets it do.
 *
 * Each step's footer is its own set of buttons (keyed by step), so a button never turns into
 * another under the operator's pointer: "Back to the join code" can never submit the code.
 */
export function AddServerDialog({ open, onClose }: AddServerDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const { data: session } = useQuery(sessionQuery());
  const [step, setStep] = useState<Step>("authorize");
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [access, setAccess] = useState<Access>("admin");
  const [key, setKey] = useState<NodeKey | null>(null);
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const [address, setAddress] = useState("");
  const [addressError, setAddressError] = useState<string | null>(null);
  const [added, setAdded] = useState<NodeRecord | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  // The fingerprint of the key shown in step 1, with the key it is of.
  const [fingerprinted, setFingerprinted] = useState<{ key: string; value: string | null } | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const addressRef = useRef<HTMLInputElement>(null);
  const nameFormId = useId();

  const reading = code.trim() === "" ? null : classifyJoinCode(code);
  const centralName = key === null ? null : centralNameFrom(key.authorize_command);

  // The fingerprint of the key shown in step 1, to check the pasted code was made for it.
  useEffect(() => {
    if (key === null) return;
    let current = true;
    void sshFingerprint(key.public_key).then((value) => {
      if (current) setFingerprinted({ key: key.public_key, value });
    });
    return () => {
      current = false;
    };
  }, [key]);
  const fingerprint = key !== null && fingerprinted?.key === key.public_key ? fingerprinted.value : null;

  const keyFetch = useMutation({
    mutationFn: (node: string) => fetchNodeKey(node),
    onSuccess: (answer) => {
      setKey(answer);
      queryClient.setQueryData(nodeKeys.key(answer.name), answer);
    },
  });

  const add = useMutation({
    mutationFn: (joinCode: string) => addNode({ name: key?.name ?? name, ssh_target: address.trim(), join_code: joinCode }),
    onSuccess: (node) => {
      // The code carries a token: it is not kept a moment longer than it is needed.
      setCode("");
      setAdded(node);
      setFailure(null);
      setStep("result");
      toast.success(t("servers.add.addedToast", { name: node.name }));
      void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
      void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
    },
    onError: (error) => {
      // Declining "Confirm it's you" is not a failed registration: the form stays as it was.
      if (error instanceof ElevationCancelledError) return;
      // A code the central refused is not sent twice: it is cleared, to be pasted again.
      if (isCodeError(error)) setCode("");
      setFailure(error);
      setStep("result");
    },
  });

  const pending = keyFetch.isPending || add.isPending;

  const reset = (): void => {
    setStep("authorize");
    setName("");
    setNameError(null);
    setAccess("admin");
    setKey(null);
    setCode("");
    setCodeError(null);
    setAddress("");
    setAddressError(null);
    setAdded(null);
    setFailure(null);
    keyFetch.reset();
    add.reset();
  };

  const onOpenChange = (next: boolean): void => {
    // Pending includes "Confirm it's you", which opens over this dialog.
    if (next || pending) return;
    onClose();
    reset();
  };

  const submitName = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (pending) return;
    const wanted = name.trim();
    if (!NODE_NAME.test(wanted)) {
      setNameError(t("servers.add.name.invalid"));
      nameRef.current?.focus();
      return;
    }
    setNameError(null);
    keyFetch.mutate(wanted);
  };

  const submitJoin = (): void => {
    if (pending) return;
    const read = classifyJoinCode(code);
    const addressOk = SSH_TARGET.test(address.trim());
    setCodeError(read.ok ? null : t(PROBLEM_WORDS[read.problem]));
    setAddressError(addressOk ? null : t("servers.add.join.addressInvalid"));
    if (!read.ok) {
      codeRef.current?.focus();
      return;
    }
    if (!addressOk) {
      addressRef.current?.focus();
      return;
    }
    add.mutate(read.code);
  };

  const onEnter = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    submitJoin();
  };

  const serverName = key?.name ?? name.trim();
  const twoFactorOff = session !== undefined && !session.totp_enabled;
  const fingerprintMatches = reading?.ok === true && fingerprint !== null ? reading.summary.centralKeyFingerprint === fingerprint : null;
  const close = (): void => {
    onOpenChange(false);
  };

  let body;
  let footer;
  if (step === "authorize") {
    body = (
      <div className="flex flex-col gap-5">
        <Notice tone="warning" title={t("servers.add.authorize.whereTitle")}>
          {t("servers.add.authorize.where", { central: session?.hostname ?? "" })}
        </Notice>
        <form id={nameFormId} noValidate onSubmit={submitName} className="flex flex-col gap-4">
          <Field
            label={t("servers.add.name.label")}
            description={t("servers.add.name.description")}
            error={nameError}
            action={
              <Button type="submit" loading={keyFetch.isPending}>
                {key === null ? t("servers.add.name.showCommand") : t("servers.add.name.again")}
              </Button>
            }
          >
            <Input
              ref={nameRef}
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              maxLength={32}
              placeholder="web-2"
              value={name}
              disabled={keyFetch.isPending}
              onValueChange={(value: string) => {
                setName(value);
                // A command printed for another name must not be run for this one.
                if (key !== null && value.trim() !== key.name) setKey(null);
              }}
            />
          </Field>
          <Field label={t("servers.add.access.label")} description={t("servers.add.access.description")}>
            <Select<Access>
              value={access}
              onValueChange={setAccess}
              options={[
                { value: "admin", label: t("fleet.access.level.admin"), hint: t("servers.add.access.adminHint") },
                { value: "deploy", label: t("fleet.access.level.deploy"), hint: t("servers.add.access.deployHint") },
                { value: "read", label: t("fleet.access.level.read"), hint: t("servers.add.access.readHint") },
              ]}
            />
          </Field>
        </form>
        {keyFetch.isError ? <ErrorBlock live compact error={keyFetch.error} title={t("servers.add.name.keyFailed")} /> : null}
        {key !== null ? (
          <div className="flex flex-col gap-4">
            <p className="text-14 text-pretty text-fg">{t("servers.add.authorize.intro", { name: key.name })}</p>
            <AuthorizeCommand t={t} name={key.name} command={authorizeCommand(key.authorize_command, access)} />
            <div className="flex items-start gap-2.5 rounded-control border border-border px-3 py-2.5">
              <ICONS.locked aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
              <div className="flex min-w-0 flex-col gap-0.5">
                <p className="text-13 font-medium text-fg">{t("servers.add.authorize.safeTitle")}</p>
                <p className="text-13 text-pretty text-fg-muted">{t("servers.add.authorize.safeBody", { name: key.name })}</p>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    );
    footer = (
      <Fragment key="authorize">
        <WizardActions
          className="w-full"
          back={{ label: t("servers.add.cancel"), onClick: close }}
          next={{
            label: t("servers.add.next"),
            onClick: () => {
              setStep("join");
              requestAnimationFrame(() => codeRef.current?.focus());
            },
          }}
          {...(key === null ? { missing: t("servers.add.authorize.missing"), onMissing: () => nameRef.current?.focus() } : {})}
        />
      </Fragment>
    );
  } else if (step === "join") {
    body = (
      <div className="flex flex-col gap-5">
        <p className="text-14 text-pretty text-fg">{t("servers.add.join.intro", { name: serverName })}</p>
        <Field label={t("servers.add.join.codeLabel")} description={t("servers.add.join.codeDescription", { name: serverName })} error={codeError}>
          <Input
            ref={codeRef}
            type="password"
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder={JOIN_PLACEHOLDER}
            value={code}
            disabled={add.isPending}
            onKeyDown={onEnter}
            onValueChange={(value: string) => {
              setCode(value);
              if (codeError !== null) setCodeError(null);
            }}
          />
        </Field>
        {reading !== null && !reading.ok && reading.problem !== "empty" && codeError === null ? (
          <Notice tone="warning">{t(PROBLEM_WORDS[reading.problem])}</Notice>
        ) : null}
        {reading?.ok === true ? <CodeSummary t={t} summary={reading.summary} centralName={centralName} fingerprintMatches={fingerprintMatches} /> : null}
        <Field
          label={t("servers.add.join.addressLabel")}
          description={
            reading?.ok === true
              ? t("servers.add.join.addressDescriptionFrom", { user: reading.summary.sshUser, port: String(reading.summary.sshPort) })
              : t("servers.add.join.addressDescription")
          }
          error={addressError}
        >
          <Input
            ref={addressRef}
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="web2.example.com"
            value={address}
            disabled={add.isPending}
            onKeyDown={onEnter}
            onValueChange={(value: string) => {
              setAddress(value);
              if (addressError !== null) setAddressError(null);
            }}
          />
        </Field>
        <p className="text-12 text-fg-muted">{t("servers.add.join.elevationNote")}</p>
      </div>
    );
    footer = (
      <Fragment key="join">
        <WizardActions
          className="w-full"
          back={{
            label: t("servers.add.back"),
            onClick: () => {
              if (!add.isPending) setStep("authorize");
            },
          }}
          next={{ label: t("servers.add.join.submit"), loading: add.isPending, onClick: submitJoin }}
        />
      </Fragment>
    );
  } else {
    body =
      added !== null ? (
        <Added t={t} node={added} onNavigate={close} />
      ) : (
        <Failed t={t} name={serverName} error={failure} twoFactorOff={twoFactorOff} onNavigate={close} />
      );
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
              add.reset();
              setStep("join");
              requestAnimationFrame(() => codeRef.current?.focus());
            }}
          >
            {t("servers.add.result.tryAgain")}
          </Button>
        </Fragment>
      );
  }

  const steps = [
    { id: "authorize", label: t("servers.add.steps.authorize") },
    { id: "join", label: t("servers.add.steps.join") },
    { id: "result", label: t("servers.add.steps.result") },
  ];

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      initialFocus={nameRef}
      title={t("servers.add.title")}
      description={t("servers.add.description")}
      footer={footer}
    >
      <div className="flex flex-col gap-5">
        <Stepper orientation="horizontal" steps={steps} current={step} />
        {body}
      </div>
    </Dialog>
  );
}
