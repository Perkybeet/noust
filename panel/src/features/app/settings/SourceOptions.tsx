import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { sourceKeys, sourceOptionsQuery } from "../../../api/queries/sources";
import type { SourceOptions } from "../../../api/queries/sources";
import { announce } from "../../../app/Announcer";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Field } from "../../../components/ui/Field";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { Switch } from "../../../components/ui/Switch";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { splitErrors } from "../../settings/formErrors";

type Lfs = SourceOptions["lfs"];

/** One glob per line, blank lines left out: what the API takes. */
function pathsOf(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

/** The fields, starting from what is stored; keyed on the stored value so a save redraws them clean. */
function SourceOptionsForm({ domain, options }: { domain: string; options: SourceOptions }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [submodules, setSubmodules] = useState(options.submodules);
  const [lfs, setLfs] = useState<Lfs>(options.lfs);
  const [shallow, setShallow] = useState(options.shallow);
  const [skip, setSkip] = useState(options.skip_markers);
  const [paths, setPaths] = useState(options.watch_paths.join("\n"));
  const [resetting, setResetting] = useState(false);

  const changed =
    submodules !== options.submodules ||
    lfs !== options.lfs ||
    shallow !== options.shallow ||
    skip !== options.skip_markers ||
    JSON.stringify(pathsOf(paths)) !== JSON.stringify(options.watch_paths);

  const settle = (result: SourceOptions): void => {
    queryClient.setQueryData(sourceKeys.sourceOptions(domain), result);
  };
  const save = useMutation({
    mutationFn: () =>
      request("put", "/api/apps/{domain}/source-options", {
        params: { domain },
        body: { submodules, lfs, shallow, skip_markers: skip, watch_paths: pathsOf(paths) },
      }),
    onSuccess: (result) => {
      settle(result);
      announce(t("sourceOptions.saved", { domain }));
    },
  });
  const split = splitErrors(save.error, ["watch_paths", "lfs", "submodules", "shallow", "skip_markers"] as const);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (changed && !save.isPending) save.mutate();
  };

  return (
    <>
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Switch label={t("sourceOptions.submodules.label")} description={t("sourceOptions.submodules.description")} checked={submodules} onCheckedChange={setSubmodules} />
        <Field label={t("sourceOptions.lfs.label")} description={t("sourceOptions.lfs.description")} error={split.fields.lfs}>
          <Select
            value={lfs}
            onValueChange={(value) => setLfs(value)}
            options={[
              { value: "auto", label: t("sourceOptions.lfs.auto") },
              { value: "on", label: t("sourceOptions.lfs.on") },
              { value: "off", label: t("sourceOptions.lfs.off") },
            ]}
          />
        </Field>
        <Switch label={t("sourceOptions.shallow.label")} description={t("sourceOptions.shallow.description")} checked={shallow} onCheckedChange={setShallow} />
        <Switch label={t("sourceOptions.skip.label")} description={t("sourceOptions.skip.description")} checked={skip} onCheckedChange={setSkip} />
        <Field label={t("sourceOptions.paths.label")} description={t("sourceOptions.paths.description")} error={split.fields.watch_paths}>
          <Textarea mono rows={4} value={paths} placeholder={t("sourceOptions.paths.placeholder")} onChange={(event) => setPaths(event.target.value)} />
        </Field>
        <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("sourceOptions.paths.note")}</p>
        {save.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("sourceOptions.notSaved", { domain })} /> : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" size="sm" loading={save.isPending} disabled={!changed}>
            {t("sourceOptions.save")}
          </Button>
          {options.customised ? (
            <Button size="sm" variant="ghost" onClick={() => setResetting(true)}>
              {t("sourceOptions.reset")}
            </Button>
          ) : null}
        </div>
      </form>
      <ConfirmDialog
        open={resetting}
        onOpenChange={setResetting}
        friction="simple"
        server={node}
        title={t("sourceOptions.resetTitle", { domain })}
        description={t("sourceOptions.resetDescription")}
        actionLabel={t("sourceOptions.resetAction")}
        onConfirm={async () => {
          const result = await request("delete", "/api/apps/{domain}/source-options", { params: { domain } });
          settle(result);
          announce(t("sourceOptions.resetDone", { domain }));
        }}
      />
    </>
  );
}

/**
 * How the code is fetched (submodules, Git LFS, shallow clone) and which pushes deploy it (skip
 * markers, watch paths): the application's Source settings, one form saved with one button.
 * They are the same options `noust app source` sets.
 */
export function SourceOptionsCard({ domain }: { domain: string }) {
  const t = useT();
  const view = useQuery(sourceOptionsQuery(domain));
  const title = t("sourceOptions.title");
  const description = t("sourceOptions.description");
  if (view.isPending) {
    return (
      <Card title={title} description={description} loading>
        <span className="sr-only">{t("sourceOptions.loading")}</span>
        <Skeleton className="h-24 w-full rounded-control" />
      </Card>
    );
  }
  if (view.isError) {
    return (
      <Card title={title} description={description}>
        <ErrorBlock compact error={view.error} title={t("sourceOptions.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      </Card>
    );
  }
  return (
    <Card title={title} description={description}>
      <SourceOptionsForm key={JSON.stringify(view.data)} domain={domain} options={view.data} />
      <CommandHint command={`noust app source show ${domain}`} label={t("sourceOptions.fromTerminal")} />
    </Card>
  );
}
