import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { appRegistryLoginsQuery, registryKeys, registryLoginsQuery, setAppRegistryLogins } from "../../../api/queries/registries";
import type { AppRegistryLogins, RegistryLogin } from "../../../api/queries/registries";
import { announce } from "../../../app/Announcer";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Skeleton } from "../../../components/ui/Skeleton";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { kindWords } from "../../settings/registries/registries";

/** Whether registry accounts mean anything for the application: the types that pull or build images. */
export function usesRegistries(app: { app_type?: string | null }): boolean {
  return app.app_type === "docker-compose" || app.app_type === "container";
}

function Choice({ domain, logins, attached }: { domain: string; logins: readonly RegistryLogin[]; attached: AppRegistryLogins }) {
  const t = useT();
  const queryClient = useQueryClient();
  const stored = attached.logins;
  const [chosen, setChosen] = useState<readonly string[]>(stored);
  const changed = chosen.length !== stored.length || chosen.some((name) => !stored.includes(name));
  const save = useMutation({
    mutationFn: () => setAppRegistryLogins(domain, [...chosen]),
    onSuccess: (result) => {
      queryClient.setQueryData(registryKeys.forApp(domain), result);
      void queryClient.invalidateQueries({ queryKey: registryKeys.list });
      announce(t("registries.app.saved", { domain }));
    },
    onError: (error) => {
      reportActionError(t("registries.app.saveFailed"), error);
    },
  });
  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-col gap-2">
        {logins.map((login) => (
          <li key={login.name}>
            <Checkbox
              label={login.name}
              description={`${kindWords(t, login.kind)} - ${login.server} - ${login.username}`}
              checked={chosen.includes(login.name)}
              onCheckedChange={(on) => {
                setChosen((now) => (on ? [...now, login.name] : now.filter((name) => name !== login.name)));
              }}
            />
          </li>
        ))}
      </ul>
      <div>
        <Button variant="primary" size="sm" loading={save.isPending} disabled={!changed} onClick={() => save.mutate()}>
          {t("registries.app.save")}
        </Button>
      </div>
    </div>
  );
}

/**
 * The registry accounts an application pulls and builds with, chosen among the ones the server
 * holds (added in Settings, Integrations). They apply from the next deployment or update.
 */
export function RegistryLoginsCard({ domain }: { domain: string }) {
  const t = useT();
  const all = useQuery(registryLoginsQuery());
  const attached = useQuery(appRegistryLoginsQuery(domain));
  const title = t("registries.app.title");
  const description = t("registries.app.description");
  if (all.isPending || attached.isPending) {
    return (
      <Card title={title} description={description} loading>
        <span className="sr-only">{t("registries.app.loading")}</span>
        <Skeleton className="h-16 w-full rounded-control" />
      </Card>
    );
  }
  const failure = all.error ?? attached.error;
  if (all.isError || attached.isError) {
    return (
      <Card title={title} description={description}>
        <ErrorBlock
          compact
          error={failure}
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
    <Card title={title} description={description}>
      {all.data.logins.length === 0 ? (
        <p className="max-w-measure text-13 text-pretty text-fg-muted">
          {t("registries.app.none")}{" "}
          <TextLink to="/settings/integrations" size="ui">
            {t("registries.app.manage")}
          </TextLink>
        </p>
      ) : (
        <Choice key={attached.data.logins.join("\n")} domain={domain} logins={all.data.logins} attached={attached.data} />
      )}
      <CommandHint command={`noust registry attach ${domain} NAME`} label={t("registries.app.fromTerminal")} />
    </Card>
  );
}
