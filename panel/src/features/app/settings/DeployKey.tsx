import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, RefreshCw } from "lucide-react";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { deployKeyQuery, deployKeysQuery, sourceKeys } from "../../../api/queries/sources";
import type { AppDeployKey, DeployKey } from "../../../api/queries/sources";
import { announce } from "../../../app/Announcer";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { CopyValue } from "../../../components/ui/CopyValue";
import { Dialog } from "../../../components/ui/Dialog";
import { ExternalLink } from "../../../components/ui/ExternalLink";
import { FeatureState } from "../../../components/ui/FeatureState";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { splitErrors } from "../../settings/formErrors";
import { reportActionError } from "../../apps/useAppActions";
import { useSudoFirst } from "./formParts";
import { webhookStatusQuery } from "./queries";
import { deployKeysUrl } from "./sourceForge";
import { forgeName } from "./webhook";

/** What an action on the key answers: the application's key as the API now sees it. */
function useKeySettled(domain: string): (result: AppDeployKey) => void {
  const queryClient = useQueryClient();
  return (result) => {
    queryClient.setQueryData(sourceKeys.deployKey(domain), result);
    // The server-wide list and the other applications of the same repository read the same key.
    void queryClient.invalidateQueries({ queryKey: sourceKeys.deployKeys });
  };
}

/** The key's facts, one per row; the fingerprint is what to compare with the code host's. */
function keyFacts(key: DeployKey, t: T): KeyValueItem[] {
  return [
    { label: t("sources.deployKey.facts.name"), value: key.name },
    { label: t("sources.deployKey.facts.type"), value: key.key_type },
    {
      label: t("sources.deployKey.facts.origin"),
      value: key.origin === "imported" ? t("sources.deployKey.origin.imported") : t("sources.deployKey.origin.generated"),
      mono: false,
      copy: false,
    },
    { label: t("sources.deployKey.facts.fingerprint"), value: key.fingerprint },
    { label: t("sources.deployKey.facts.created"), value: <RelativeTime value={key.created_at} />, mono: false, copy: false },
    ...(key.rotated_at ? [{ label: t("sources.deployKey.facts.rotated"), value: <RelativeTime value={key.rotated_at} />, mono: false, copy: false as const }] : []),
    {
      label: t("sources.deployKey.facts.lastUsed"),
      value: <RelativeTime value={key.last_used_at} fallback={t("sources.deployKey.facts.never")} />,
      mono: false,
      copy: false,
    },
  ];
}

/** The place of a replacement in its two steps, with the new key to add and a way to finish it. */
function Rotation({
  name,
  pending,
  forge,
  keysUrl,
  repository,
  testing,
  onTest,
  onCancel,
  cancelling,
}: {
  name: string;
  pending: NonNullable<DeployKey["pending"]>;
  forge: string;
  keysUrl: string | null;
  repository: string;
  testing: boolean;
  onTest: () => void;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const t = useT();
  return (
    <div className="rounded-card border border-border bg-bg-sunken p-4">
      <Subsection
        level={4}
        title={t("sources.deployKey.rotation.title")}
        description={t("sources.deployKey.rotation.description")}
        actions={<StatusPill state="queued" label={t("sources.deployKey.rotation.state", { forge })} size="sm" />}
      >
        <ol className="flex flex-col gap-4">
          <li className="flex min-w-0 flex-col gap-2">
            <p className="text-13 font-medium text-fg">{t("sources.deployKey.rotation.step1Title")}</p>
            <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("sources.deployKey.rotation.step1Body", { forge })}</p>
            <CopyValue
              value={pending.public_key}
              label={t("sources.deployKey.copyPublicKey")}
              name={t("sources.deployKey.rotation.newKeyLabel", { name })}
            />
            <p className="text-12 text-fg-muted">
              {t.rich("sources.deployKey.rotation.newFingerprintLine", { fingerprint: <Mono key="fingerprint">{pending.fingerprint}</Mono> })}{" "}
              {t.rich("sources.deployKey.rotation.since", { time: <RelativeTime key="since" value={pending.since} /> })}
            </p>
            {keysUrl !== null ? (
              <ExternalLink href={keysUrl} inline>
                {t("sources.deployKey.addAtForge", { repository, forge })}
              </ExternalLink>
            ) : null}
          </li>
          <li className="flex min-w-0 flex-col gap-2">
            <p className="text-13 font-medium text-fg">{t("sources.deployKey.rotation.step2Title")}</p>
            <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("sources.deployKey.rotation.step2Body", { forge })}</p>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" loading={testing} onClick={onTest}>
                {t("sources.deployKey.rotation.testNew")}
              </Button>
              <Button size="sm" variant="ghost" loading={cancelling} onClick={onCancel}>
                {t("sources.deployKey.rotation.cancel")}
              </Button>
            </div>
          </li>
        </ol>
      </Subsection>
    </div>
  );
}

/** Pasting a private key that already exists: kept on the server, never shown again. */
function ImportKeyDialog({
  domain,
  repository,
  open,
  onOpenChange,
  onImported,
}: {
  domain: string;
  repository: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported: (result: AppDeployKey) => void;
}) {
  const t = useT();
  const [privateKey, setPrivateKey] = useState("");
  const [name, setName] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const run = useMutation({
    mutationFn: () =>
      request("post", "/api/apps/{domain}/deploy-key/import", {
        params: { domain },
        body: { private_key: privateKey, ...(name.trim() !== "" ? { name: name.trim() } : {}) },
      }),
    onSuccess: (result) => {
      onImported(result);
      announce(t("sources.deployKey.importDialog.imported", { domain }));
      close();
    },
  });
  const split = splitErrors(run.error, ["private_key", "name"] as const);
  const keyError = split.fields.private_key ?? (submitted && privateKey.trim() === "" ? t("sources.deployKey.importDialog.keyRequired") : undefined);

  // The private key leaves this component's state the moment the dialog closes, whatever closed it.
  function close(): void {
    setPrivateKey("");
    setName("");
    setSubmitted(false);
    run.reset();
    onOpenChange(false);
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (privateKey.trim() === "" || run.isPending) return;
    run.mutate();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) onOpenChange(true);
        else if (!run.isPending) close();
      }}
      size="md"
      title={t("sources.deployKey.importDialog.title", { repository })}
      description={t("sources.deployKey.importDialog.description")}
      footer={
        <>
          <Button disabled={run.isPending} onClick={close}>
            {t("appSettings.cancel")}
          </Button>
          <Button type="submit" form="import-deploy-key" variant="primary" loading={run.isPending}>
            {t("sources.deployKey.importDialog.submit")}
          </Button>
        </>
      }
    >
      <form id="import-deploy-key" onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field label={t("sources.deployKey.importDialog.keyLabel")} description={t("sources.deployKey.importDialog.keyDescription")} error={keyError}>
          <Textarea
            mono
            rows={8}
            autoComplete="off"
            autoCapitalize="off"
            value={privateKey}
            onChange={(event) => setPrivateKey(event.target.value)}
            placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
          />
        </Field>
        <Field label={t("sources.deployKey.importDialog.nameLabel")} description={t("sources.deployKey.importDialog.nameDescription")} error={split.fields.name} optional>
          <Input mono autoComplete="off" value={name} onValueChange={(value: string) => setName(value)} />
        </Field>
        {run.isError && split.form !== null ? (
          <ErrorBlock live compact error={split.form} title={t("sources.deployKey.importDialog.notImported")} />
        ) : null}
      </form>
    </Dialog>
  );
}

/** Choosing a key that exists for this repository too: one machine user's key can reach several. */
function ExistingKeyDialog({
  domain,
  repository,
  keys,
  open,
  onOpenChange,
  onAssigned,
}: {
  domain: string;
  repository: string;
  keys: readonly DeployKey[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAssigned: (result: AppDeployKey) => void;
}) {
  const t = useT();
  const [chosen, setChosen] = useState("");
  const run = useMutation({
    mutationFn: (key: string) => request("post", "/api/apps/{domain}/deploy-key", { params: { domain }, body: { key } }),
    onSuccess: (result, key) => {
      onAssigned(result);
      announce(t("sources.deployKey.existingDialog.assigned", { domain, name: key }));
      close();
    },
  });

  function close(): void {
    setChosen("");
    run.reset();
    onOpenChange(false);
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (chosen === "" || run.isPending) return;
    run.mutate(chosen);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) onOpenChange(true);
        else if (!run.isPending) close();
      }}
      size="md"
      title={t("sources.deployKey.existingDialog.title", { repository })}
      description={t("sources.deployKey.existingDialog.description")}
      footer={
        <>
          <Button disabled={run.isPending} onClick={close}>
            {t("appSettings.cancel")}
          </Button>
          <Button type="submit" form="assign-deploy-key" variant="primary" loading={run.isPending} disabled={chosen === ""}>
            {t("sources.deployKey.existingDialog.submit")}
          </Button>
        </>
      }
    >
      <form id="assign-deploy-key" onSubmit={submit} noValidate className="flex flex-col gap-4">
        {keys.length === 0 ? (
          <p className="text-13 text-fg-muted">{t("sources.deployKey.existingDialog.none")}</p>
        ) : (
          <Field label={t("sources.deployKey.existingDialog.keyLabel")} nativeLabel={false}>
            <Select
              mono
              placeholder={t("sources.deployKey.existingDialog.keyPlaceholder")}
              value={chosen === "" ? null : chosen}
              onValueChange={setChosen}
              options={keys.map((key) => ({
                value: key.name,
                label: t("sources.deployKey.existingDialog.optionLine", { name: key.name, fingerprint: key.fingerprint }),
                ...((key.repositories ?? []).length > 0 ? { hint: (key.repositories ?? []).join(", ") } : {}),
              }))}
            />
          </Field>
        )}
        {run.isError ? <ErrorBlock live compact error={run.error} title={t("sources.deployKey.existingDialog.notAssigned")} /> : null}
      </form>
    </Dialog>
  );
}

/**
 * How the repository is reached over SSH: the deploy key it has (its public half to add at the
 * code host, with copy), a way to make, import or choose one, to test it as a deploy would, to
 * replace it without a moment when the repository has none that works, and to remove it. The
 * private half is never on screen: the API never sends it.
 */
export function DeployKeyCard({ domain, repository }: { domain: string; repository: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const sudoFirst = useSudoFirst();
  const settle = useKeySettled(domain);
  const view = useQuery(deployKeyQuery(domain));
  const keys = useQuery(deployKeysQuery());
  // Which code host this is, and where its deploy keys are added, is the webhook setup's reading
  // of the same source; the card works without it.
  const webhook = useQuery(webhookStatusQuery(domain));
  const forge = forgeName(webhook.data?.forge.forge, t.locale);
  const keysUrl = deployKeysUrl(webhook.data?.forge);

  const [importing, setImporting] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);

  const generate = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/deploy-key", { params: { domain }, body: {} }),
    onSuccess: (result) => {
      settle(result);
      announce(t("sources.deployKey.generated", { domain }));
    },
    onError: (error: unknown) => reportActionError(t("sources.deployKey.notGenerated", { domain }), error),
  });
  const start = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/deploy-key/rotate", { params: { domain } }),
    onSuccess: (result) => {
      setAnswer(null);
      settle(result);
      announce(t("sources.deployKey.rotation.started", { domain }));
    },
    onError: (error: unknown) => reportActionError(t("sources.deployKey.rotation.notStarted", { domain }), error),
  });
  const cancel = useMutation({
    mutationFn: () => request("delete", "/api/apps/{domain}/deploy-key/rotate", { params: { domain } }),
    onSuccess: (result) => {
      settle(result);
      announce(t("sources.deployKey.rotation.cancelled", { domain }));
    },
    onError: (error: unknown) => reportActionError(t("sources.deployKey.rotation.notCancelled"), error),
  });
  const test = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/deploy-key/test", { params: { domain } }),
    onSuccess: (result) => {
      setAnswer(result.message);
      // A waiting key the host accepted is the key now: read it again, it is what was tested.
      void queryClient.invalidateQueries({ queryKey: sourceKeys.deployKey(domain) });
      void queryClient.invalidateQueries({ queryKey: sourceKeys.deployKeys });
    },
  });

  const title = t("sources.deployKey.title");
  const description = t("sources.deployKey.description");
  if (view.isPending) {
    return (
      <Card title={title} description={description} loading>
        <span className="sr-only">{t("sources.deployKey.loading")}</span>
        <Skeleton className="h-16 w-full rounded-control" />
      </Card>
    );
  }
  if (view.isError) {
    return (
      <Card title={title} description={description}>
        <ErrorBlock compact error={view.error} title={t("sources.deployKey.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      </Card>
    );
  }

  const data = view.data;
  const key = data.key ?? null;
  const sshUrl = data.ssh_url ?? "";
  const others = key === null ? [] : (key.applications ?? []).filter((app) => app !== domain);
  const choices = (keys.data?.keys ?? []).filter((candidate) => candidate.name !== key?.name);

  const elevated = (then: () => void): void => {
    sudoFirst(then, t("sources.deployKey.notChanged", { domain }));
  };

  return (
    <Card title={title} description={description}>
      <div className="flex flex-col gap-4">
        {key === null ? (
          <>
            <FeatureState
              state="off"
              title={t("sources.deployKey.stateNone")}
              action={
                <Button size="sm" icon={<KeyRound aria-hidden="true" />} loading={generate.isPending} onClick={() => generate.mutate()}>
                  {t("sources.deployKey.generate")}
                </Button>
              }
            >
              {data.server_key_fingerprint
                ? t.rich("sources.deployKey.noneBodyServerKey", {
                    repository: <Mono>{repository}</Mono>,
                    fingerprint: <Mono>{data.server_key_fingerprint}</Mono>,
                  })
                : t("sources.deployKey.noneBodyNoKey")}
            </FeatureState>
            {!data.source_is_ssh && sshUrl !== "" ? (
              <Notice>{t.rich("sources.deployKey.httpsHint", { sshUrl: <Mono>{sshUrl}</Mono> })}</Notice>
            ) : null}
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="ghost" onClick={() => elevated(() => setImporting(true))}>
                {t("sources.deployKey.import")}
              </Button>
              {choices.length > 0 ? (
                <Button size="sm" variant="ghost" onClick={() => setChoosing(true)}>
                  {t("sources.deployKey.useExisting")}
                </Button>
              ) : null}
            </div>
          </>
        ) : (
          <>
            <FeatureState state={data.source_is_ssh ? "on" : "problem"} title={data.source_is_ssh ? t("sources.deployKey.stateOwn") : t("sources.deployKey.stateUnused")}>
              {data.source_is_ssh
                ? t.rich("sources.deployKey.ownBody", { repository: <Mono>{repository}</Mono> })
                : t.rich("sources.deployKey.unusedBody", { sshUrl: <Mono>{sshUrl}</Mono> })}
            </FeatureState>
            <KeyValueList items={keyFacts(key, t)} />
            <Subsection level={4} title={t("sources.deployKey.publicKeyTitle")} description={t("sources.deployKey.publicKeyBody", { forge })}>
              <CopyValue
                value={key.public_key}
                label={t("sources.deployKey.copyPublicKey")}
                name={t("sources.deployKey.publicKeyLabel", { name: key.name })}
              />
              {keysUrl !== null ? (
                <ExternalLink href={keysUrl} inline>
                  {t("sources.deployKey.addAtForge", { repository, forge })}
                </ExternalLink>
              ) : null}
            </Subsection>
            {others.length > 0 ? (
              <p className="max-w-measure text-13 text-pretty text-fg-muted">
                {t.rich("sources.deployKey.sharedNote", { apps: <Mono key="apps">{others.join(", ")}</Mono> })}
              </p>
            ) : null}
            {key.pending ? (
              <Rotation
                name={key.name}
                pending={key.pending}
                forge={forge}
                keysUrl={keysUrl}
                repository={repository}
                testing={test.isPending}
                onTest={() => test.mutate()}
                onCancel={() => cancel.mutate()}
                cancelling={cancel.isPending}
              />
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" loading={test.isPending} onClick={() => test.mutate()}>
                  {t("sources.deployKey.test")}
                </Button>
                <Button size="sm" icon={<RefreshCw aria-hidden="true" />} loading={start.isPending} onClick={() => start.mutate()}>
                  {t("sources.deployKey.rotate")}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => elevated(() => setRemoving(true))}>
                  {t("sources.deployKey.remove")}
                </Button>
              </div>
            )}
            {test.isPending ? (
              <p role="status" className="text-13 text-fg-muted">
                {t("sources.deployKey.testing")}
              </p>
            ) : test.isError ? (
              <ErrorBlock live compact error={test.error} title={t("sources.deployKey.testFailed")} />
            ) : answer !== null ? (
              <Notice tone="success" live title={t("sources.deployKey.testedTitle")}>
                {answer}
              </Notice>
            ) : null}
          </>
        )}
      </div>
      <ImportKeyDialog domain={domain} repository={repository} open={importing} onOpenChange={setImporting} onImported={settle} />
      <ExistingKeyDialog domain={domain} repository={repository} keys={choices} open={choosing} onOpenChange={setChoosing} onAssigned={settle} />
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        friction="simple"
        server={node}
        title={t("sources.deployKey.removeDialog.title", { domain })}
        description={t("sources.deployKey.removeDialog.description", { forge })}
        actionLabel={t("sources.deployKey.removeDialog.action")}
        onConfirm={async () => {
          const result = await request("delete", "/api/apps/{domain}/deploy-key", { params: { domain } });
          setAnswer(null);
          settle(result);
          announce(t("sources.deployKey.removeDialog.removed", { domain }));
        }}
      />
    </Card>
  );
}
