import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";

import { appRegistryLoginsQuery, registryKeys, registryLoginsQuery, setAppRegistryLogins } from "../../../api/queries/registries";
import type { AppRegistryLogins, RegistryLogins } from "../../../api/queries/registries";
import { announce } from "../../../app/Announcer";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { kindWords } from "../../settings/registries/registries";
import type { FormPart } from "./formParts";
import { SETTINGS_ICONS } from "./objectIcons";

/** Whether registry accounts mean anything for the application: the types that pull or build images. */
export function usesRegistries(app: { app_type?: string | null }): boolean {
  return app.app_type === "docker-compose" || app.app_type === "container";
}

export interface RegistryChoicePart {
  /** The save bar's part; null until the attached accounts are read. */
  part: FormPart | null;
  all: UseQueryResult<RegistryLogins>;
  attached: UseQueryResult<AppRegistryLogins>;
  chosen: readonly string[];
  toggle: (name: string, on: boolean) => void;
}

/**
 * The registry accounts an application pulls and builds with, chosen among the ones the server
 * holds (added in Settings, Integrations): a part of the form of the page that shows them, saved
 * from its one save bar. They apply from the next deployment or update.
 */
export function useRegistryChoice(domain: string, enabled = true): RegistryChoicePart {
  const t = useT();
  const queryClient = useQueryClient();
  const all = useQuery({ ...registryLoginsQuery(), enabled });
  const attached = useQuery({ ...appRegistryLoginsQuery(domain), enabled });
  const stored = attached.data?.logins ?? null;
  const [baseline, setBaseline] = useState<readonly string[]>([]);
  const [chosen, setChosen] = useState<readonly string[]>([]);
  const same = (a: readonly string[], b: readonly string[]): boolean => a.length === b.length && a.every((name) => b.includes(name));

  // The accounts changed underneath an untouched choice (saved here, or from a terminal): follow them.
  if (stored !== null && !same(stored, baseline)) {
    setBaseline(stored);
    if (same(chosen, baseline)) setChosen(stored);
  }

  const save = useMutation({
    mutationFn: () => setAppRegistryLogins(domain, [...chosen]),
    onSuccess: (result) => {
      queryClient.setQueryData(registryKeys.forApp(domain), result);
      void queryClient.invalidateQueries({ queryKey: registryKeys.list });
      announce(t("registries.app.saved", { domain }));
    },
  });

  return {
    part:
      stored === null
        ? null
        : {
            changes: same(chosen, stored) ? 0 : 1,
            elevated: false,
            check: () => true,
            save: async () => {
              await save.mutateAsync();
            },
            discard: () => {
              setChosen(stored);
              save.reset();
            },
          },
    all,
    attached,
    chosen,
    toggle: (name, on) => setChosen((now) => (on ? [...now, name] : now.filter((candidate) => candidate !== name))),
  };
}

/** The accounts the server holds, ticked for the ones this application uses. */
export function RegistryLoginsCard({ choice }: { choice: RegistryChoicePart }) {
  const t = useT();
  const { all, attached } = choice;
  const title = t("registries.app.title");
  const description = t("registries.app.description");
  if (all.isPending || attached.isPending) {
    return (
      <LoadingRegion label={t("registries.app.loading")}>
        <SkeletonCard lines={2} />
      </LoadingRegion>
    );
  }
  if (all.isError || attached.isError) {
    return (
      <Card title={title} icon={SETTINGS_ICONS.registry} description={description}>
        <ErrorBlock
          compact
          error={all.error ?? attached.error}
          title={t("registries.app.readFailed")}
          onRetry={() => {
            void all.refetch();
            void attached.refetch();
          }}
        />
      </Card>
    );
  }
  return (
    <Card title={title} icon={SETTINGS_ICONS.registry} description={description}>
      {all.data.logins.length === 0 ? (
        <p className="max-w-measure text-13 text-pretty text-fg-muted">
          {t("registries.app.none")}{" "}
          <TextLink to="/settings/integrations" size="ui">
            {t("registries.app.manage")}
          </TextLink>
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {all.data.logins.map((login) => (
            <li key={login.name}>
              <Checkbox
                label={login.name}
                description={`${kindWords(t, login.kind)} - ${login.server} - ${login.username}`}
                checked={choice.chosen.includes(login.name)}
                onCheckedChange={(on) => choice.toggle(login.name, on)}
              />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
