import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { connectGitLab, gitlabKeys, saveGitLabOAuthApp, startGitLabSignIn } from "../../../api/queries/gitlab";
import type { GitLabStatus } from "../../../api/queries/gitlab";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { CopyValue } from "../../../components/ui/CopyValue";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { splitErrors } from "../formErrors";
import { goToGitLab, normalizeHost, oauthAppFor } from "./gitlab";

type Way = "token" | "oauth";

const FIELDS = ["name", "host", "token", "client_id", "client_secret"] as const;

export interface ConnectGitLabDialogProps {
  open: boolean;
  status: GitLabStatus;
  onClose: () => void;
}

/**
 * Connecting GitLab, one of two ways. With an access token the server checks it against
 * GitLab at once. With a sign-in, the browser goes to GitLab to approve this server and comes
 * back to the console's callback page; that needs an OAuth application registered in GitLab
 * for the host, which is recorded here the first time (its secret is kept, never shown).
 */
export function ConnectGitLabDialog({ open, status, onClose }: ConnectGitLabDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const nameRef = useRef<HTMLInputElement>(null);
  const [way, setWay] = useState<Way>("token");
  const [name, setName] = useState("");
  const [host, setHost] = useState("gitlab.com");
  const [token, setToken] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  // An address the browser was not sent to, because it is not https: shown instead of followed.
  const [refused, setRefused] = useState<string | null>(null);
  const registered = oauthAppFor(status, host);

  const byToken = useMutation({
    mutationFn: () => connectGitLab({ name: name.trim(), host: normalizeHost(host), token }),
    onSuccess: (connection) => {
      toast.success(t("gitlab.dialog.connected", { name: connection.name, host: connection.host }));
      void queryClient.invalidateQueries({ queryKey: gitlabKeys.all });
      onClose();
    },
  });
  const byOAuth = useMutation({
    mutationFn: async () => {
      const checked = normalizeHost(host);
      if (registered === undefined) await saveGitLabOAuthApp({ host: checked, client_id: clientId.trim(), client_secret: clientSecret });
      return startGitLabSignIn(name.trim(), checked);
    },
    onSuccess: (started) => {
      void queryClient.invalidateQueries({ queryKey: gitlabKeys.status });
      if (!goToGitLab(started.authorize_url)) setRefused(started.authorize_url);
    },
  });
  const run = way === "token" ? byToken : byOAuth;
  const errors = splitErrors(run.error, FIELDS);
  const pending = run.isPending || (way === "oauth" && byOAuth.isSuccess && refused === null);

  const close = (): void => {
    if (!run.isPending) onClose();
  };
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (pending) return;
    setRefused(null);
    run.mutate();
  };
  const filled = name.trim() !== "" && normalizeHost(host) !== "";
  const ready =
    way === "token" ? filled && token !== "" : filled && status.redirect_uri !== null && (registered !== undefined || (clientId.trim() !== "" && clientSecret !== ""));

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      size="md"
      initialFocus={nameRef}
      title={t("gitlab.dialog.title")}
      description={t("gitlab.dialog.description")}
      footer={
        <>
          <Button disabled={run.isPending} onClick={close}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={pending} disabled={!ready}>
            {way === "token" ? t("gitlab.dialog.tokenAction") : registered === undefined ? t("gitlab.dialog.registerAndSignIn") : t("gitlab.dialog.signInAction")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <span className="text-13 font-medium text-fg">{t("gitlab.dialog.way")}</span>
          <SegmentedControl
            label={t("gitlab.dialog.way")}
            value={way}
            onValueChange={(next) => {
              setWay(next);
              byToken.reset();
              byOAuth.reset();
            }}
            options={[
              { value: "token", label: t("gitlab.signIn.token") },
              { value: "oauth", label: t("gitlab.signIn.oauth") },
            ]}
            className="self-start"
          />
        </div>
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("gitlab.dialog.failed")} /> : null}
        <Field label={t("common.words.name")} description={t("gitlab.dialog.nameDescription")} error={errors.fields.name}>
          <Input ref={nameRef} mono autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={63} value={name} onValueChange={setName} />
        </Field>
        <Field label={t("gitlab.dialog.host")} description={t("gitlab.dialog.hostDescription")} error={errors.fields.host}>
          <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={253} value={host} onValueChange={setHost} />
        </Field>
        {way === "token" ? (
          <Field label={t("gitlab.dialog.token")} description={t("gitlab.dialog.tokenDescription")} error={errors.fields.token}>
            <Input mono type="password" autoComplete="new-password" spellCheck={false} maxLength={512} value={token} onValueChange={setToken} />
          </Field>
        ) : (
          <>
            <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("gitlab.dialog.oauthHelp")}</p>
            {status.redirect_uri === null ? (
              <Notice tone="warning">
                <p className="text-pretty">{t("gitlab.dialog.noRedirect")}</p>
                <CommandHint command="noust config set web.public_url https://console.example.com" />
              </Notice>
            ) : registered === undefined ? (
              <>
                <CopyValue value={status.redirect_uri} label={t("gitlab.dialog.redirect")} name={t("gitlab.dialog.redirect")} />
                <p className="text-12 text-fg-muted">{t("gitlab.dialog.scopes")}</p>
                <Field label={t("gitlab.dialog.clientId")} error={errors.fields.client_id}>
                  <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={256} value={clientId} onValueChange={setClientId} />
                </Field>
                <Field label={t("gitlab.dialog.clientSecret")} error={errors.fields.client_secret}>
                  <Input mono type="password" autoComplete="new-password" spellCheck={false} maxLength={512} value={clientSecret} onValueChange={setClientSecret} />
                </Field>
              </>
            ) : (
              <Notice tone="success">{t("gitlab.dialog.registered", { host: registered.host })}</Notice>
            )}
            {refused !== null ? <ErrorBlock live compact error={{ detail: refused }} title={t("gitlab.dialog.notHttps")} hint={t("gitlab.dialog.notHttpsHint")} /> : null}
            {byOAuth.isSuccess && refused === null ? (
              <p role="status" className="text-13 text-fg-muted">
                {t("gitlab.dialog.opening")}
              </p>
            ) : null}
          </>
        )}
      </form>
    </Dialog>
  );
}
