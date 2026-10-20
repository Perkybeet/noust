import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { sourceKeys, sourceOptionsQuery } from "../../../api/queries/sources";
import type { SourceOptions } from "../../../api/queries/sources";
import { announce } from "../../../app/Announcer";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Field } from "../../../components/ui/Field";
import { Select } from "../../../components/ui/Select";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { splitErrors } from "../../settings/formErrors";
import type { FormPart } from "./formParts";

type Lfs = SourceOptions["lfs"];

/** The fields as typed: the globs are one per line. */
interface Draft {
  submodules: boolean;
  lfs: Lfs;
  shallow: boolean;
  skip: boolean;
  paths: string;
}

/** One glob per line, blank lines left out: what the API takes. */
function pathsOf(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

function draftOf(options: SourceOptions): Draft {
  return { submodules: options.submodules, lfs: options.lfs, shallow: options.shallow, skip: options.skip_markers, paths: options.watch_paths.join("\n") };
}

/** How many of the fields differ from what is stored. */
function changedFields(draft: Draft, stored: Draft): number {
  return [
    draft.submodules !== stored.submodules,
    draft.lfs !== stored.lfs,
    draft.shallow !== stored.shallow,
    draft.skip !== stored.skip,
    JSON.stringify(pathsOf(draft.paths)) !== JSON.stringify(pathsOf(stored.paths)),
  ].filter(Boolean).length;
}

export interface SourceOptionsPart {
  /** The save bar's part; null until the options are read. */
  part: FormPart | null;
  view: UseQueryResult<SourceOptions>;
  draft: Draft;
  set: (next: Partial<Draft>) => void;
  errors: Partial<Record<"watch_paths" | "lfs" | "submodules" | "shallow" | "skip_markers", string>>;
  unplaced: unknown;
  /** Back to the defaults, asked about first. */
  reset: () => Promise<void>;
}

/**
 * How the code is fetched (submodules, Git LFS, shallow clone) and which pushes deploy it (skip
 * markers, watch paths): the application's Source settings, one form saved from the page's save
 * bar. They are the same options `noust app source` sets.
 */
export function useSourceOptions(domain: string): SourceOptionsPart {
  const t = useT();
  const queryClient = useQueryClient();
  const view = useQuery(sourceOptionsQuery(domain));
  const stored = view.data !== undefined ? draftOf(view.data) : null;
  const [baseline, setBaseline] = useState<Draft | null>(null);
  const [draft, setDraft] = useState<Draft>({ submodules: false, lfs: "auto", shallow: false, skip: false, paths: "" });

  // The options changed underneath an untouched form (saved here, or from a terminal): follow them.
  if (stored !== null && (baseline === null || changedFields(stored, baseline) > 0)) {
    setBaseline(stored);
    if (baseline === null || changedFields(draft, baseline) === 0) setDraft(stored);
  }

  const settle = (result: SourceOptions): void => {
    queryClient.setQueryData(sourceKeys.sourceOptions(domain), result);
    setBaseline(draftOf(result));
    setDraft(draftOf(result));
  };
  const save = useMutation({
    mutationFn: () =>
      request("put", "/api/apps/{domain}/source-options", {
        params: { domain },
        body: { submodules: draft.submodules, lfs: draft.lfs, shallow: draft.shallow, skip_markers: draft.skip, watch_paths: pathsOf(draft.paths) },
      }),
    onSuccess: (result) => {
      settle(result);
      announce(t("sourceOptions.saved", { domain }));
    },
  });
  const split = splitErrors(save.error, ["watch_paths", "lfs", "submodules", "shallow", "skip_markers"] as const);

  return {
    part:
      stored === null
        ? null
        : {
            changes: changedFields(draft, stored),
            elevated: false,
            check: () => true,
            save: async () => {
              await save.mutateAsync();
            },
            discard: () => {
              setDraft(stored);
              save.reset();
            },
          },
    view,
    draft,
    set: (next) => {
      setDraft((previous) => ({ ...previous, ...next }));
      if (save.isError) save.reset();
    },
    errors: split.fields,
    unplaced: save.isError ? split.form : null,
    reset: async () => {
      const result = await request("delete", "/api/apps/{domain}/source-options", { params: { domain } });
      settle(result);
      announce(t("sourceOptions.resetDone", { domain }));
    },
  };
}

/** The card of the git options: fetching and the pushes that deploy, with the way back to the defaults. */
export function SourceOptionsCard({ domain, options }: { domain: string; options: SourceOptionsPart }) {
  const t = useT();
  const { node } = useNode();
  const [resetting, setResetting] = useState(false);
  const { view, draft, set, errors } = options;
  const title = t("sourceOptions.title");
  const description = t("sourceOptions.description");
  if (view.isPending) {
    return (
      <LoadingRegion label={t("sourceOptions.loading")}>
        <SkeletonCard lines={4} />
      </LoadingRegion>
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
    <Card
      title={title}
      description={description}
      actions={
        view.data.customised ? (
          <Button size="sm" variant="ghost" onClick={() => setResetting(true)}>
            {t("sourceOptions.reset")}
          </Button>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-4">
        <Checkbox label={t("sourceOptions.submodules.label")} description={t("sourceOptions.submodules.description")} checked={draft.submodules} onCheckedChange={(submodules) => set({ submodules })} />
        <Field label={t("sourceOptions.lfs.label")} description={t("sourceOptions.lfs.description")} error={errors.lfs}>
          <Select
            value={draft.lfs}
            onValueChange={(lfs) => set({ lfs })}
            options={[
              { value: "auto", label: t("sourceOptions.lfs.auto") },
              { value: "on", label: t("sourceOptions.lfs.on") },
              { value: "off", label: t("sourceOptions.lfs.off") },
            ]}
          />
        </Field>
        <Checkbox label={t("sourceOptions.shallow.label")} description={t("sourceOptions.shallow.description")} checked={draft.shallow} onCheckedChange={(shallow) => set({ shallow })} />
        <Checkbox label={t("sourceOptions.skip.label")} description={t("sourceOptions.skip.description")} checked={draft.skip} onCheckedChange={(skip) => set({ skip })} />
        <Field label={t("sourceOptions.paths.label")} description={`${t("sourceOptions.paths.description")} ${t("sourceOptions.paths.note")}`} error={errors.watch_paths}>
          <Textarea mono rows={4} value={draft.paths} placeholder={t("sourceOptions.paths.placeholder")} onChange={(event) => set({ paths: event.target.value })} />
        </Field>
        {options.unplaced !== null ? <ErrorBlock live compact error={options.unplaced} title={t("sourceOptions.notSaved", { domain })} /> : null}
      </div>
      <ConfirmDialog
        open={resetting}
        onOpenChange={setResetting}
        friction="simple"
        server={node}
        title={t("sourceOptions.resetTitle", { domain })}
        description={t("sourceOptions.resetDescription")}
        actionLabel={t("sourceOptions.resetAction")}
        onConfirm={options.reset}
      />
    </Card>
  );
}
