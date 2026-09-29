import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Check, ShieldCheck } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { ElevationCancelledError, isApiError } from "../../../api/client";
import { sessionQuery } from "../../../api/queries/auth";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { CopyTextButton } from "../../../components/ui/CopyTextButton";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { ServerLink } from "../../fleet/links";
import { NODE_NAME, addNode, fetchNodeKey, nodeKeys, nodeStatus, sshAddress } from "../../fleet/nodes";
import type { NodeKey, NodeRecord } from "../../fleet/nodes";
import { ReachabilityPill } from "../../fleet/ReachabilityPill";

/** `host`, `user@host` or `user@host:port`; an IPv6 host goes in brackets. */
const SSH_TARGET = /^(?:[^@\s:[\]]+@)?(?:[^@\s:[\]]+|\[[0-9a-fA-F:.]+\])(?::\d{1,5})?$/;

type Step = 1 | 2 | 3;
const TOTAL = 3;

/** Where the operator is in the three steps, as an ordered list a screen reader can walk. */
function Steps({ t, step }: { t: T; step: Step }) {
  const names = [t("servers.add.steps.authorize"), t("servers.add.steps.join"), t("servers.add.steps.result")];
  return (
    <ol aria-label={t("servers.add.progressLabel")} className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-2">
      {names.map((name, index) => {
        const number = (index + 1) as Step;
        const state = number < step ? "done" : number === step ? "current" : "todo";
        return (
          <li key={name} {...(state === "current" ? { "aria-current": "step" as const } : {})} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className={cx(
                "flex size-5 items-center justify-center rounded-pill border text-12 font-medium",
                state === "done" && "border-accent bg-accent text-on-accent",
                state === "current" && "border-accent text-accent-fg",
                state === "todo" && "border-border text-fg-faint",
              )}
            >
              {state === "done" ? <Check className="size-3" /> : number}
            </span>
            <span className={cx("text-13", state === "todo" ? "text-fg-faint" : "font-medium text-fg")}>
              <span aria-hidden="true">{name}</span>
              <span className="sr-only">
                {state === "done"
                  ? t("servers.add.stepDone", { name })
                  : state === "current"
                    ? t("servers.add.stepCurrent", { name })
                    : t("servers.add.stepTodo", { name })}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** The command to run on the server, whole and wrapped: a key cut off cannot be checked by eye. */
function AuthorizeCommand({ t, name, command }: { t: T; name: string; command: string }) {
  const labelId = useId();
  return (
    <div className="flex flex-col gap-4">
      <p className="text-14 text-pretty text-fg">{t("servers.add.authorize.intro", { name })}</p>
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
          <CopyTextButton value={command} variant="primary">
            {t("servers.add.authorize.copy")}
          </CopyTextButton>
        </div>
      </div>
      <div className="flex items-start gap-2.5 rounded-control border border-border bg-surface px-3 py-2.5">
        <ShieldCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-muted" />
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="text-13 font-medium text-fg">{t("servers.add.authorize.safeTitle")}</p>
          <p className="text-13 text-pretty text-fg-muted">{t("servers.add.authorize.safeBody", { name })}</p>
        </div>
      </div>
    </div>
  );
}

function Added({ t, node, onNavigate }: { t: T; node: NodeRecord; onNavigate: () => void }) {
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
            value: node.version ? (
              <span translate="no" className="mono">
                {node.version}
              </span>
            ) : (
              t("servers.add.result.notReported")
            ),
            copy: node.version ?? false,
            mono: node.version !== null && node.version !== undefined,
          },
          {
            label: t("servers.add.result.address"),
            value: (
              <span translate="no" className="mono">
                {sshAddress(node)}
              </span>
            ),
            copy: sshAddress(node),
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

export interface AddServerDialogProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Adding a server, the inverted way: the central never logs in to it. (1) Name it and run the
 * command the central prints on the server itself, which authorizes a key that can only
 * forward the console's port; (2) paste the join code it printed and its SSH address; (3) the
 * central pins its host key, opens the tunnel and checks the token, and says how that went.
 */
export function AddServerDialog({ open, onClose }: AddServerDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const { data: session } = useQuery(sessionQuery());
  const [step, setStep] = useState<Step>(1);
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [key, setKey] = useState<NodeKey | null>(null);
  const [code, setCode] = useState("");
  const [address, setAddress] = useState("");
  const [addressError, setAddressError] = useState<string | null>(null);
  const [added, setAdded] = useState<NodeRecord | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const nameFormId = useId();
  const joinFormId = useId();

  const keyFetch = useMutation({
    mutationFn: (node: string) => fetchNodeKey(node),
    onSuccess: (answer) => {
      setKey(answer);
      queryClient.setQueryData(nodeKeys.key(answer.name), answer);
    },
  });

  const add = useMutation({
    mutationFn: () => addNode({ name: key?.name ?? name, ssh_target: address.trim(), join_code: code.trim() }),
    onSuccess: (node) => {
      // The code carries a token: it is not kept a moment longer than it is needed.
      setCode("");
      setAdded(node);
      setFailure(null);
      setStep(3);
      toast.success(t("servers.add.addedToast", { name: node.name }));
      void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
    },
    onError: (error) => {
      // Declining "Confirm it's you" is not a failed registration: the form stays as it was.
      if (error instanceof ElevationCancelledError) return;
      setFailure(error);
      setStep(3);
    },
  });

  const pending = keyFetch.isPending || add.isPending;

  const reset = (): void => {
    setStep(1);
    setName("");
    setNameError(null);
    setKey(null);
    setCode("");
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

  const submitJoin = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (pending) return;
    if (!SSH_TARGET.test(address.trim())) {
      setAddressError(t("servers.add.join.addressInvalid"));
      return;
    }
    setAddressError(null);
    add.mutate();
  };

  const serverName = key?.name ?? name.trim();
  const twoFactorOff = session !== undefined && !session.totp_enabled;

  let body;
  let footer;
  if (step === 1) {
    body = (
      <div className="flex flex-col gap-5">
        <form id={nameFormId} noValidate onSubmit={submitName} className="flex flex-col gap-3">
          <Field label={t("servers.add.name.label")} description={t("servers.add.name.description")} error={nameError}>
            <Input
              ref={nameRef}
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              maxLength={32}
              value={name}
              disabled={keyFetch.isPending}
              onValueChange={(value: string) => {
                setName(value);
                // A command printed for another name must not be run for this one.
                if (key !== null && value.trim() !== key.name) setKey(null);
              }}
            />
          </Field>
          {key === null ? (
            <div>
              <Button type="submit" loading={keyFetch.isPending} disabled={name.trim() === ""}>
                {t("servers.add.name.showCommand")}
              </Button>
            </div>
          ) : null}
        </form>
        {keyFetch.isError ? <ErrorBlock live compact error={keyFetch.error} title={t("servers.add.name.keyFailed")} /> : null}
        {key !== null ? <AuthorizeCommand t={t} name={key.name} command={key.authorize_command} /> : null}
      </div>
    );
    footer = (
      <>
        <Button
          onClick={() => {
            onOpenChange(false);
          }}
        >
          {t("servers.add.cancel")}
        </Button>
        <Button
          variant="primary"
          disabled={key === null}
          onClick={() => {
            setStep(2);
            requestAnimationFrame(() => codeRef.current?.focus());
          }}
        >
          {t("servers.add.next")}
        </Button>
      </>
    );
  } else if (step === 2) {
    body = (
      <form id={joinFormId} noValidate onSubmit={submitJoin} className="flex flex-col gap-5">
        <p className="text-14 text-pretty text-fg">{t("servers.add.join.intro", { name: serverName })}</p>
        <Field label={t("servers.add.join.codeLabel")} description={t("servers.add.join.codeDescription", { name: serverName })}>
          <Input
            ref={codeRef}
            type="password"
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            value={code}
            disabled={add.isPending}
            onValueChange={(value: string) => {
              setCode(value);
            }}
          />
        </Field>
        <Field label={t("servers.add.join.addressLabel")} description={t("servers.add.join.addressDescription")} error={addressError}>
          <Input
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="root@web2.example.com"
            value={address}
            disabled={add.isPending}
            onValueChange={(value: string) => {
              setAddress(value);
              if (addressError !== null) setAddressError(null);
            }}
          />
        </Field>
        <p className="text-12 text-fg-muted">{t("servers.add.join.elevationNote")}</p>
      </form>
    );
    footer = (
      <>
        <Button
          disabled={add.isPending}
          onClick={() => {
            setStep(1);
          }}
        >
          {t("servers.add.back")}
        </Button>
        <Button type="submit" form={joinFormId} variant="primary" loading={add.isPending} disabled={code.trim() === "" || address.trim() === ""}>
          {t("servers.add.join.submit")}
        </Button>
      </>
    );
  } else {
    const close = (): void => {
      onOpenChange(false);
    };
    body =
      added !== null ? (
        <Added t={t} node={added} onNavigate={close} />
      ) : (
        <Failed t={t} name={serverName} error={failure} twoFactorOff={twoFactorOff} onNavigate={close} />
      );
    footer =
      added !== null ? (
        <Button variant="primary" onClick={close}>
          {t("servers.add.done")}
        </Button>
      ) : (
        <>
          <Button onClick={close}>{t("servers.add.cancel")}</Button>
          <Button
            variant="primary"
            onClick={() => {
              setFailure(null);
              add.reset();
              setStep(2);
              requestAnimationFrame(() => codeRef.current?.focus());
            }}
          >
            {t("servers.add.result.tryAgain")}
          </Button>
        </>
      );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      initialFocus={nameRef}
      title={t("servers.add.title")}
      description={t("servers.add.stepOf", { step, total: TOTAL })}
      footer={footer}
    >
      <Steps t={t} step={step} />
      {body}
    </Dialog>
  );
}
