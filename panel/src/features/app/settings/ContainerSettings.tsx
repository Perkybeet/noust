import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { isApiError, request } from "../../../api/client";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { Card } from "../../../components/ui/Card";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { ContainerFields } from "../../container/ContainerFields";
import { containerProblems, draftOf, plannedChange } from "../../container/model";
import type { ContainerDraft, ContainerSettings as Settings } from "../../container/model";
import { useSaveBar } from "./formParts";
import { RegistryLoginsCard, useRegistryChoice } from "./RegistryLogins";
import { containerQuery, settingsKeys } from "./queries";
import { useSettingsApp, useSubsectionIcon, useSubsectionTitle } from "./settingsApp";
import { LoadingRegion } from "../../../components/page";

/** How the image comes to exist, in the words a person reads. */
function builtFrom(t: T, settings: Settings): string {
  if (settings.method === "image") return t("containers.settings.fromImage");
  return t(settings.method === "railpack" ? "containers.settings.fromRailpack" : "containers.settings.fromDockerfile");
}

/** What the application is now, apart from what the form edits: where it is built from and what serves. */
function facts(t: T, settings: Settings): KeyValueItem[] {
  return [
    { label: t("containers.settings.builtFrom"), value: builtFrom(t, settings), mono: false, copy: false },
    ...(settings.release !== null && settings.release !== undefined ? [{ label: t("containers.settings.release"), value: settings.release }] : []),
    ...(settings.digest !== null && settings.digest !== undefined
      ? [{ label: t("containers.settings.digest"), value: settings.digest, hint: t("containers.settings.digestHint") }]
      : []),
  ];
}

function Form({ settings, domain }: { settings: Settings; domain: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const stored = draftOf(settings);
  const [baseline, setBaseline] = useState(settings);
  const [draft, setDraft] = useState<ContainerDraft>(stored);
  const [submitted, setSubmitted] = useState(false);

  // The settings changed underneath an untouched form (saved here, or from a terminal): follow them.
  if (settings !== baseline) {
    setBaseline(settings);
    if (plannedChange(draft, baseline).fields.length === 0) setDraft(draftOf(settings));
  }

  const planned = plannedChange(draft, settings);
  const clientErrors = containerProblems(draft, { existing: true });
  const save = useMutation({
    mutationFn: () => request("put", "/api/apps/{domain}/container", { params: { domain }, body: planned.body }),
    onSuccess: (result) => {
      queryClient.setQueryData(settingsKeys.container(domain), result);
      // What the server keeps now is what the form says, and no secret typed here stays in it.
      setDraft(draftOf(result));
      setBaseline(result);
      setSubmitted(false);
    },
  });
  const serverErrors: Record<string, string> = isApiError(save.error) && save.error.fields !== null ? save.error.fields : {};
  const errors = { ...(submitted ? clientErrors : {}), ...serverErrors };
  const placed = Object.keys(serverErrors).length > 0;
  // The accounts the image is pulled and built with are chosen beside the rest of how it is made.
  const registries = useRegistryChoice(domain);

  const bar = useSaveBar(
    [
      {
        changes: planned.fields.length,
        elevated: false,
        check: () => {
          setSubmitted(true);
          return Object.keys(clientErrors).length === 0;
        },
        save: async () => {
          await save.mutateAsync();
        },
        discard: () => {
          setDraft(stored);
          setSubmitted(false);
          save.reset();
        },
      },
      registries.part,
    ],
    t("containers.settings.notSavedFor", { domain }),
  );

  return (
    <>
      <Card padding="none">
        <div className="px-4 py-1">
          <KeyValueList items={facts(t, settings)} />
        </div>
      </Card>
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("containers.settings.takesEffect")}</p>
      <Card>
        <ContainerFields
          draft={draft}
          errors={errors}
          existing
          showImage
          onChange={(next) => {
            setDraft(next);
            if (save.isError) save.reset();
          }}
        />
      </Card>
      <RegistryLoginsCard choice={registries} />
      {save.isError && !placed ? <ErrorBlock live compact error={save.error} title={t("containers.settings.saveFailed", { domain })} /> : null}
      <CommandHint command={`noust app container set ${domain} --container-port 3000 --volume /data`} label={t("appSettings.fromTerminal")} />
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
    </>
  );
}

/**
 * Container (T3 subsection of a container application): how its image is made (a Dockerfile,
 * Railpack or a registry image), the build arguments and build secrets, the port, the volumes,
 * the restart policy and the registry account. A change is stored at once and takes effect with
 * the next update, which writes the compose file again; nothing is recreated by saving. Secrets
 * are write-only here: the password of a registry account and the value of a build secret are
 * typed, sent, and never read back.
 */
export function ContainerSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("appSettings.nav.container", domain);
  const headerIcon = useSubsectionIcon("appSettings.nav.container");
  const isContainer = app.app_type === "container";
  const container = useQuery({ ...containerQuery(domain), enabled: isContainer });

  return (
    <Section title={t("containers.settings.title")} {...headerIcon} description={t("containers.settings.description")}>
      {!isContainer ? (
        <EmptyState variant="inline" title={t("containers.settings.notContainer")} />
      ) : container.data === undefined ? (
        container.isError ? (
          <ErrorBlock error={container.error} title={t("containers.settings.readFailed")} onRetry={() => void container.refetch()} retrying={container.isRefetching} />
        ) : (
          <LoadingRegion label={t("containers.settings.loading")} className="flex flex-col gap-3">
            <Skeleton className="h-24 w-full rounded-card" />
            <Skeleton className="h-64 w-full rounded-card" />
          </LoadingRegion>
        )
      ) : (
        <Form settings={container.data} domain={domain} />
      )}
    </Section>
  );
}
