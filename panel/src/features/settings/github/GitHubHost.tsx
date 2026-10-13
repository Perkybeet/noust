import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { githubKeys, setGitHubHost } from "../../../api/queries/github";
import type { GitHubStatus } from "../../../api/queries/github";
import { announce } from "../../../app/Announcer";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { useT } from "../../../i18n";
import { splitErrors } from "../formErrors";

/** The default: nothing to type. */
const GITHUB_COM = "github.com";

/**
 * The host a status names. A server older than the field answers none, and that is github.com:
 * a console served by a newer Noust than the server it manages must not fail on it.
 */
export function statusHost(status: Pick<GitHubStatus, "host">): string {
  return (status as { host?: string }).host ?? GITHUB_COM;
}

/** What the field holds for a host: empty for github.com, which is what leaving it empty means. */
export function hostField(host: string): string {
  return host === GITHUB_COM ? "" : host;
}

/**
 * Which GitHub the integration talks to, said before the App is created: github.com, or the
 * address of a GitHub Enterprise Server or Cloud. Once an App exists its host is fixed (the
 * server refuses the change), so this is shown only while there is none.
 */
export function GitHubHost({ status }: { status: GitHubStatus }) {
  const t = useT();
  const queryClient = useQueryClient();
  const stored = hostField(statusHost(status));
  const [host, setHost] = useState(stored);
  const save = useMutation({
    mutationFn: () => setGitHubHost(host.trim() === "" ? null : host.trim()),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: githubKeys.all });
      announce(t("githubHost.saved", { host: result.host }));
    },
  });
  const split = splitErrors(save.error, ["host"] as const, "host");
  const changed = host.trim() !== stored;

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (changed && !save.isPending) save.mutate();
  };

  return (
    <Card>
      <form onSubmit={submit} noValidate className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-col gap-1">
        <p className="text-14 font-medium text-fg">{t("githubHost.title")}</p>
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("githubHost.description")}</p>
      </div>
      <Field label={t("githubHost.label")} description={t("githubHost.fieldDescription")} error={split.fields.host} optional className="sm:max-w-80">
        <Input
          mono
          value={host}
          onValueChange={setHost}
          placeholder={t("githubHost.placeholder")}
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          disabled={save.isPending}
        />
      </Field>
      {save.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("githubHost.failed")} /> : null}
      <div>
        <Button type="submit" size="sm" loading={save.isPending} disabled={!changed}>
          {t("githubHost.save")}
        </Button>
      </div>
      <CommandHint command="noust github host set ghe.example.com" label={t("githubHost.fromTerminal")} />
      </form>
    </Card>
  );
}
