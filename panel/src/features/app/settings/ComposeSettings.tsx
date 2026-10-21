import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { announce } from "../../../app/Announcer";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { splitErrors } from "../../settings/formErrors";
import { joinWords, splitNames, splitWords } from "./composeWords";
import type { FormPart } from "./formParts";
import { composeSettingsQuery, settingsKeys } from "./queries";
import type { ComposeSettings } from "./queries";
import { SETTINGS_ICONS } from "./objectIcons";

/** The fields of a Compose stack's settings as typed: lists are words separated by spaces. */
interface Draft {
  file: string;
  profiles: string;
  buildArgs: string;
  upArgs: string;
  services: string;
}

const EMPTY: Draft = { file: "", profiles: "", buildArgs: "", upArgs: "", services: "" };

/** The form for the settings a stack has; a list the server leaves out is empty. */
function draftOf(settings: ComposeSettings): Draft {
  return {
    file: settings.compose_file ?? "",
    profiles: (settings.profiles ?? []).join(" "),
    buildArgs: joinWords(settings.build_args ?? []),
    upArgs: joinWords(settings.up_args ?? []),
    services: (settings.services ?? []).join(" "),
  };
}

/** What the form sends: each list read the way the backend reads it. */
function bodyOf(draft: Draft) {
  return {
    compose_file: draft.file.trim(),
    profiles: splitNames(draft.profiles),
    build_args: splitWords(draft.buildArgs),
    up_args: splitWords(draft.upArgs),
    services: splitNames(draft.services),
  };
}

/** How many of the form's fields mean something other than what is stored. */
function changedFields(draft: Draft, stored: Draft): number {
  const now = bodyOf(draft);
  const was = bodyOf(stored);
  return [now.compose_file !== was.compose_file, ...(["profiles", "build_args", "up_args", "services"] as const).map((key) => JSON.stringify(now[key]) !== JSON.stringify(was[key]))].filter(Boolean).length;
}

export interface ComposeOptionsPart {
  /** The save bar's part; null until the settings are read. */
  part: FormPart | null;
  view: UseQueryResult<ComposeSettings>;
  draft: Draft;
  edit: (field: keyof Draft) => (value: string) => void;
  errors: Partial<Record<"compose_file" | "profiles" | "build_args" | "up_args" | "services", string>>;
  unplaced: unknown;
  /** Back to what the unit recorded, asked about first. */
  reset: () => Promise<void>;
  customised: boolean;
}

/**
 * A Compose stack's file, profiles and the closed list of arguments of `docker compose build` and
 * `up`, with the services they address: the same as `noust app compose`. A part of the Compose
 * form, saved from its one save bar. Changing them asks "Confirm it's you", since the arguments run
 * as root.
 */
export function useComposeOptions(domain: string): ComposeOptionsPart {
  const t = useT();
  const queryClient = useQueryClient();
  const view = useQuery(composeSettingsQuery(domain));
  const stored = view.data !== undefined ? draftOf(view.data) : null;
  const [baseline, setBaseline] = useState<Draft>(EMPTY);
  const [draft, setDraft] = useState<Draft>(EMPTY);

  // The settings changed underneath an untouched form (saved here, or from a terminal): follow them.
  if (stored !== null && changedFields(stored, baseline) > 0) {
    setBaseline(stored);
    if (changedFields(draft, baseline) === 0) setDraft(stored);
  }

  const settle = (result: ComposeSettings): void => {
    queryClient.setQueryData(settingsKeys.compose(domain), result);
    setBaseline(draftOf(result));
    setDraft(draftOf(result));
  };
  const save = useMutation({
    mutationFn: () => request("put", "/api/apps/{domain}/compose", { params: { domain }, body: bodyOf(draft) }),
    onSuccess: (result) => {
      settle(result);
      announce(t("composeSettings.saved", { domain }));
    },
  });
  const split = splitErrors(save.error, ["compose_file", "profiles", "build_args", "up_args", "services"] as const);

  return {
    part:
      stored === null
        ? null
        : {
            changes: changedFields(draft, stored),
            elevated: true,
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
    edit: (field) => (value) => {
      setDraft((previous) => ({ ...previous, [field]: value }));
      if (save.isError) save.reset();
    },
    errors: split.fields,
    unplaced: save.isError ? split.form : null,
    reset: async () => {
      const result = await request("delete", "/api/apps/{domain}/compose", { params: { domain } });
      settle(result);
      announce(t("composeSettings.resetDone", { domain }));
    },
    customised: view.data?.customised === true,
  };
}

/** The card of the stack's options: what the form holds, and the way back to what the unit recorded. */
export function ComposeSettingsCard({ domain, options }: { domain: string; options: ComposeOptionsPart }) {
  const t = useT();
  const { node } = useNode();
  const [resetting, setResetting] = useState(false);
  const title = t("composeSettings.title");
  const { view, draft, edit, errors } = options;

  if (view.isPending) {
    return (
      <LoadingRegion label={t("composeSettings.loading")}>
        <SkeletonCard lines={5} />
      </LoadingRegion>
    );
  }
  if (view.isError) {
    return (
      <Card title={title} icon={SETTINGS_ICONS.compose}>
        <ErrorBlock compact error={view.error} title={t("composeSettings.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      </Card>
    );
  }
  const allowedBuild = view.data.allowed_build_args.join(" ");
  const allowedUp = view.data.allowed_up_args.join(" ");
  return (
    <Card
      icon={SETTINGS_ICONS.compose}
      title={title}
      description={t("composeSettings.description")}
      actions={
        options.customised ? (
          <Button icon={<SETTINGS_ICONS.reset aria-hidden="true" />} size="sm" variant="ghost" onClick={() => setResetting(true)}>
            {t("composeSettings.reset")}
          </Button>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t("composeSettings.file.label")} description={t("composeSettings.file.description")} error={errors.compose_file} optional>
          <Input mono value={draft.file} placeholder={t("composeSettings.file.placeholder")} onValueChange={edit("file")} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field label={t("composeSettings.profiles.label")} description={t("composeSettings.profiles.description")} error={errors.profiles} optional>
          <Input mono value={draft.profiles} onValueChange={edit("profiles")} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field label={t("composeSettings.buildArgs.label")} description={t("composeSettings.buildArgs.description", { allowed: allowedBuild })} error={errors.build_args} optional>
          <Input mono value={draft.buildArgs} onValueChange={edit("buildArgs")} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field label={t("composeSettings.upArgs.label")} description={t("composeSettings.upArgs.description", { allowed: allowedUp })} error={errors.up_args} optional>
          <Input mono value={draft.upArgs} onValueChange={edit("upArgs")} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field label={t("composeSettings.services.label")} description={t("composeSettings.services.description")} error={errors.services} optional>
          <Input mono value={draft.services} onValueChange={edit("services")} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        {options.unplaced !== null ? <ErrorBlock live compact error={options.unplaced} title={t("composeSettings.notSaved", { domain })} /> : null}
      </div>
      <ConfirmDialog
        open={resetting}
        onOpenChange={setResetting}
        friction="simple"
        server={node}
        title={t("composeSettings.resetTitle", { domain })}
        description={t("composeSettings.resetDescription")}
        actionLabel={t("composeSettings.resetAction")}
        onConfirm={options.reset}
      />
    </Card>
  );
}
