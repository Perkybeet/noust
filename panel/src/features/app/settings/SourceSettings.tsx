import { useQuery } from "@tanstack/react-query";

import { deployKeyQuery } from "../../../api/queries/sources";
import { CommandHint } from "../../../components/page/CommandHint";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { Notice } from "../../../components/ui/Notice";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { DeployKeyCard } from "./DeployKey";
import { ChangeSourceCard } from "./ChangeSource";
import { GitCredentialCard } from "./GitCredential";
import { SourceOptionsCard, useSourceOptions } from "./SourceOptions";
import { useSaveBar } from "./formParts";
import { useSettingsApp, useSubsectionIcon, useSubsectionTitle } from "./settingsApp";

/**
 * Source: where the code comes from and how this server signs in to it. The repository or directory
 * the application is built from, a key of its own for the repository when it is cloned over SSH, a
 * user and a token kept as a secret when it is cloned over https, and how the code is fetched (the
 * git options, the one form of the page, saved from its save bar). An application that is not built
 * from a git repository has neither key nor credential, and says so instead of showing controls that
 * do nothing.
 */
export function SourceSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("appSettings.nav.source", domain);
  const headerIcon = useSubsectionIcon("appSettings.nav.source");
  // The same read the deploy key card makes: one request, and it names the repository.
  const view = useQuery(deployKeyQuery(domain));
  const repository = view.data?.repository ?? null;
  const options = useSourceOptions(domain);
  const bar = useSaveBar([repository === null ? null : options.part], t("sourceOptions.notSaved", { domain }));

  return (
    <>
      <Section title={t("sources.title")} {...headerIcon} description={t("sources.description")}>
        {view.isPending ? (
          <LoadingRegion label={t("sources.loading")}>
            <SkeletonCard lines={3} />
          </LoadingRegion>
        ) : view.isError ? (
          <ErrorBlock error={view.error} title={t("sources.deployKey.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
        ) : repository === null ? (
          <>
            <Notice title={t("sources.notGit.title")}>{t("sources.notGit.body")}</Notice>
            <ChangeSourceCard app={app} />
          </>
        ) : (
          <>
            <ChangeSourceCard app={app} />
            <DeployKeyCard domain={domain} repository={repository} />
            <GitCredentialCard domain={domain} repository={repository} />
            <SourceOptionsCard domain={domain} options={options} />
            <p className="max-w-measure text-13 text-pretty text-fg-muted">
              {t.rich("sources.allKeys", {
                link: (
                  <TextLink key="integrations" to="/settings/integrations" size="inline">
                    {t("sources.allKeysLink")}
                  </TextLink>
                ),
              })}{" "}
              {t.rich("sources.webhookNote", {
                link: (
                  <TextLink key="push" to="/apps/$domain/settings/deploy-on-push" params={{ domain }} size="inline">
                    {t("sources.webhookLink")}
                  </TextLink>
                ),
              })}
            </p>
          </>
        )}
        <CommandHint command={`noust app source show ${domain}`} label={t("sources.fromTerminal")} />
      </Section>
      {repository === null ? null : <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />}
    </>
  );
}
