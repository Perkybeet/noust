import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { announce } from "../../../app/Announcer";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { splitErrors } from "../../settings/formErrors";
import { joinWords, splitNames, splitWords } from "./composeWords";
import { composeSettingsQuery, settingsKeys } from "./queries";
import type { ComposeSettings } from "./queries";

/** The fields of a Compose stack's settings, starting from what is stored. */
function ComposeForm({ domain, settings }: { domain: string; settings: ComposeSettings }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  // What is stored; a list the server leaves out is empty.
  const stored = {
    file: settings.compose_file ?? "",
    profiles: settings.profiles ?? [],
    buildArgs: settings.build_args ?? [],
    upArgs: settings.up_args ?? [],
    services: settings.services ?? [],
  };
  const [file, setFile] = useState(stored.file);
  const [profiles, setProfiles] = useState(stored.profiles.join(" "));
  const [buildArgs, setBuildArgs] = useState(joinWords(stored.buildArgs));
  const [upArgs, setUpArgs] = useState(joinWords(stored.upArgs));
  const [services, setServices] = useState(stored.services.join(" "));
  const [resetting, setResetting] = useState(false);

  const body = {
    compose_file: file.trim(),
    profiles: splitNames(profiles),
    build_args: splitWords(buildArgs),
    up_args: splitWords(upArgs),
    services: splitNames(services),
  };
  const changed =
    body.compose_file !== stored.file ||
    JSON.stringify(body.profiles) !== JSON.stringify(stored.profiles) ||
    JSON.stringify(body.build_args) !== JSON.stringify(stored.buildArgs) ||
    JSON.stringify(body.up_args) !== JSON.stringify(stored.upArgs) ||
    JSON.stringify(body.services) !== JSON.stringify(stored.services);

  const settle = (result: ComposeSettings): void => {
    queryClient.setQueryData(settingsKeys.compose(domain), result);
  };
  const save = useMutation({
    mutationFn: () => request("put", "/api/apps/{domain}/compose", { params: { domain }, body }),
    onSuccess: (result) => {
      settle(result);
      announce(t("composeSettings.saved", { domain }));
    },
  });
  const split = splitErrors(save.error, ["compose_file", "profiles", "build_args", "up_args", "services"] as const);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (changed && !save.isPending) save.mutate();
  };

  return (
    <>
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field label={t("composeSettings.file.label")} description={t("composeSettings.file.description")} error={split.fields.compose_file} optional>
          <Input mono value={file} placeholder={t("composeSettings.file.placeholder")} onValueChange={setFile} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field label={t("composeSettings.profiles.label")} description={t("composeSettings.profiles.description")} error={split.fields.profiles} optional>
          <Input mono value={profiles} onValueChange={setProfiles} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field
          label={t("composeSettings.buildArgs.label")}
          description={t("composeSettings.buildArgs.description", { allowed: settings.allowed_build_args.join(" ") })}
          error={split.fields.build_args}
          optional
        >
          <Input mono value={buildArgs} onValueChange={setBuildArgs} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field
          label={t("composeSettings.upArgs.label")}
          description={t("composeSettings.upArgs.description", { allowed: settings.allowed_up_args.join(" ") })}
          error={split.fields.up_args}
          optional
        >
          <Input mono value={upArgs} onValueChange={setUpArgs} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field label={t("composeSettings.services.label")} description={t("composeSettings.services.description")} error={split.fields.services} optional>
          <Input mono value={services} onValueChange={setServices} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        {save.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("composeSettings.notSaved", { domain })} /> : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" size="sm" loading={save.isPending} disabled={!changed}>
            {t("composeSettings.save")}
          </Button>
          {settings.customised ? (
            <Button size="sm" variant="ghost" onClick={() => setResetting(true)}>
              {t("composeSettings.reset")}
            </Button>
          ) : null}
        </div>
      </form>
      <ConfirmDialog
        open={resetting}
        onOpenChange={setResetting}
        friction="simple"
        server={node}
        title={t("composeSettings.resetTitle", { domain })}
        description={t("composeSettings.resetDescription")}
        actionLabel={t("composeSettings.resetAction")}
        onConfirm={async () => {
          const result = await request("delete", "/api/apps/{domain}/compose", { params: { domain } });
          settle(result);
          announce(t("composeSettings.resetDone", { domain }));
        }}
      />
    </>
  );
}

/**
 * A Compose stack's file, profiles and the closed list of arguments of `docker compose build`
 * and `up`, with the services they address: the same as `noust app compose`. Changing them asks
 * "Confirm it's you", since the arguments run as root.
 */
export function ComposeSettingsCard({ domain }: { domain: string }) {
  const t = useT();
  const view = useQuery(composeSettingsQuery(domain));
  const title = t("composeSettings.title");
  const description = t("composeSettings.description");
  if (view.isPending) {
    return (
      <Card title={title} description={description} loading>
        <span className="sr-only">{t("composeSettings.loading")}</span>
        <Skeleton className="h-24 w-full rounded-control" />
      </Card>
    );
  }
  if (view.isError) {
    return (
      <Card title={title} description={description}>
        <ErrorBlock compact error={view.error} title={t("composeSettings.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      </Card>
    );
  }
  return (
    <Card title={title} description={description}>
      <ComposeForm key={JSON.stringify(view.data)} domain={domain} settings={view.data} />
      <CommandHint command={`noust app compose show ${domain}`} label={t("composeSettings.fromTerminal")} />
    </Card>
  );
}
