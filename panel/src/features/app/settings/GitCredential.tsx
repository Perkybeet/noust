import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { gitCredentialQuery, sourceKeys } from "../../../api/queries/sources";
import type { GitCredential } from "../../../api/queries/sources";
import { announce } from "../../../app/Announcer";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { splitErrors } from "../../settings/formErrors";
import { useSudoFirst } from "./formParts";
import { webhookStatusQuery } from "./queries";
import { SETTINGS_ICONS } from "./objectIcons";

/** What the API says an application's credential is now: kept or not, never the token. */
function useCredentialSettled(domain: string): (result: GitCredential) => void {
  const queryClient = useQueryClient();
  return (result) => {
    queryClient.setQueryData(sourceKeys.gitCredential(domain), result);
  };
}

/**
 * The user and the token, typed once. The token goes to the server in this one request and is
 * dropped from this component the moment the dialog closes, however it closed: nothing here
 * can show it again, and the API has no way to send it back.
 */
function CredentialDialog({
  domain,
  repository,
  replacing,
  bitbucket,
  initialUser,
  open,
  onOpenChange,
  onSaved,
}: {
  domain: string;
  repository: string;
  replacing: boolean;
  bitbucket: boolean;
  initialUser: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (result: GitCredential) => void;
}) {
  const t = useT();
  const [username, setUsername] = useState(initialUser);
  const [secret, setSecret] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const run = useMutation({
    mutationFn: () => request("put", "/api/apps/{domain}/git-credential", { params: { domain }, body: { username: username.trim(), secret } }),
    onSuccess: (result) => {
      onSaved(result);
      announce(t("sources.credential.dialog.saved", { domain }));
      close();
    },
  });
  const split = splitErrors(run.error, ["username", "secret"] as const);
  const userError = split.fields.username ?? (submitted && username.trim() === "" ? t("sources.credential.dialog.userRequired") : undefined);
  const secretError = split.fields.secret ?? (submitted && secret === "" ? t("sources.credential.dialog.tokenRequired") : undefined);

  function close(): void {
    setSecret("");
    setSubmitted(false);
    run.reset();
    onOpenChange(false);
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (username.trim() === "" || secret === "" || run.isPending) return;
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
      title={replacing ? t("sources.credential.dialog.replaceTitle", { repository }) : t("sources.credential.dialog.title", { repository })}
      description={t("sources.credential.dialog.description")}
      footer={
        <>
          <Button disabled={run.isPending} onClick={close}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form="git-credential" variant="primary" loading={run.isPending}>
            {t("sources.credential.dialog.submit")}
          </Button>
        </>
      }
    >
      <form id="git-credential" onSubmit={submit} noValidate className="flex flex-col gap-4">
        {bitbucket ? <InlineMessage tone="info">{t("sources.credential.dialog.bitbucket")}</InlineMessage> : null}
        <Field label={t("sources.credential.dialog.userLabel")} description={t("sources.credential.dialog.userDescription")} error={userError}>
          <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} value={username} onValueChange={(value: string) => setUsername(value)} />
        </Field>
        <Field label={t("sources.credential.dialog.tokenLabel")} description={t("sources.credential.dialog.tokenDescription")} error={secretError}>
          <Input mono type="password" autoComplete="new-password" autoCapitalize="off" spellCheck={false} value={secret} onValueChange={(value: string) => setSecret(value)} />
        </Field>
        {run.isError && split.form !== null ? (
          <ErrorBlock live compact error={split.form} title={t("sources.credential.dialog.notSaved")} />
        ) : null}
      </form>
    </Dialog>
  );
}

/**
 * How the repository is reached over https: whether a user and a token are kept for it (the
 * user is shown, the token never is), and a way to set, replace and remove them. Writing and
 * removing a secret ask for sudo mode first, before any dialog opens.
 */
export function GitCredentialCard({ domain, repository }: { domain: string; repository: string }) {
  const t = useT();
  const { node } = useNode();
  const sudoFirst = useSudoFirst();
  const settle = useCredentialSettled(domain);
  const view = useQuery(gitCredentialQuery(domain));
  const webhook = useQuery(webhookStatusQuery(domain));
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);

  const title = t("sources.credential.title");
  const description = t("sources.credential.description");
  if (view.isPending) {
    return (
      <LoadingRegion label={t("sources.credential.loading")}>
        <SkeletonCard lines={2} />
      </LoadingRegion>
    );
  }
  if (view.isError) {
    return (
      <Card title={title} icon={SETTINGS_ICONS.signIn} description={description}>
        <ErrorBlock compact error={view.error} title={t("sources.credential.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      </Card>
    );
  }

  const data = view.data;
  const host = data.host ?? repository.split("/")[0] ?? "";
  const elevated = (then: () => void): void => {
    sudoFirst(then, t("sources.credential.notChanged", { domain }));
  };
  const set = (): void => elevated(() => setEditing(true));

  // The state of the credential is the pill beside the title; the sentence under it says what it means for this repository.
  const pill = data.configured ? (
    <StatusPill state={data.source_is_https ? "running" : "warning"} size="sm" label={data.source_is_https ? t("sources.credential.stateSet") : t("sources.credential.stateUnused")} />
  ) : (
    <StatusPill state="stopped" size="sm" label={t("sources.credential.stateNone")} />
  );
  const text = data.configured
    ? data.source_is_https
      ? t.rich("sources.credential.setBody", { repository: <Mono>{repository}</Mono>, user: <Mono>{data.username ?? ""}</Mono> })
      : t("sources.credential.unusedBody")
    : t.rich("sources.credential.noneBody", { host: <Mono>{host}</Mono> });

  return (
    <Card
      icon={SETTINGS_ICONS.signIn}
      title={title}
      status={pill}
      description={text}
      {...(data.configured
        ? {}
        : {
            actions: (
              <Button icon={<SETTINGS_ICONS.edit aria-hidden="true" />} size="sm" onClick={set}>
                {t("sources.credential.set")}
              </Button>
            ),
          })}
    >
      <div className="flex flex-col gap-4">
        {!data.configured && !data.source_is_https ? <InlineMessage tone="info">{t("sources.credential.sshHint")}</InlineMessage> : null}
        {data.configured ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button icon={<SETTINGS_ICONS.rebuild aria-hidden="true" />} size="sm" onClick={set}>
              {t("sources.credential.replace")}
            </Button>
            <Button icon={<SETTINGS_ICONS.remove aria-hidden="true" />} size="sm" variant="ghost" onClick={() => elevated(() => setRemoving(true))}>
              {t("common.words.remove")}
            </Button>
          </div>
        ) : null}
      </div>
      {/* Keyed on opening: the fields start from what is stored every time, and a token typed before never survives a close. */}
      <CredentialDialog
        key={String(editing)}
        domain={domain}
        repository={repository}
        replacing={data.configured}
        bitbucket={webhook.data?.forge.forge === "bitbucket"}
        initialUser={data.username ?? ""}
        open={editing}
        onOpenChange={setEditing}
        onSaved={settle}
      />
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        friction="simple"
        server={node}
        title={t("sources.credential.removeDialog.title", { domain })}
        description={t("sources.credential.removeDialog.description", { host })}
        actionLabel={t("sources.credential.removeDialog.action")}
        onConfirm={async () => {
          const result = await request("delete", "/api/apps/{domain}/git-credential", { params: { domain } });
          settle(result);
          announce(t("sources.credential.removeDialog.removed", { domain }));
        }}
      />
    </Card>
  );
}
