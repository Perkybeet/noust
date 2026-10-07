import { useQuery } from "@tanstack/react-query";

import { deployKeyQuery } from "../../../api/queries/sources";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Card } from "../../../components/ui/Card";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { DeployKeyCard } from "./DeployKey";
import { GitCredentialCard } from "./GitCredential";
import { useSettingsApp, useSubsectionTitle } from "./settingsApp";

/** The two cards' shape while the repository is read: what is on screen never moves when it arrives. */
function SourceSkeleton() {
  const t = useT();
  return (
    <div aria-busy="true" className="flex flex-col gap-4">
      <span className="sr-only">{t("sources.loading")}</span>
      <Card loading>
        <div aria-hidden="true" className="flex flex-col gap-3">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-16 w-full rounded-control" />
        </div>
      </Card>
      <Card loading>
        <div aria-hidden="true" className="flex flex-col gap-3">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-16 w-full rounded-control" />
        </div>
      </Card>
    </div>
  );
}

/**
 * Source: how this server signs in to the repository the application is built from. A key of
 * its own for the repository when it is cloned over SSH, a user and a token kept as a secret
 * when it is cloned over https. An application that is not built from a git repository has
 * neither, and says so instead of showing controls that do nothing.
 */
export function SourceSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("appSettings.nav.source", domain);
  // The same read the deploy key card makes: one request, and it names the repository.
  const view = useQuery(deployKeyQuery(domain));
  const repository = view.data?.repository ?? null;

  return (
    <Section title={t("sources.title")} description={t("sources.description")}>
      {view.isPending ? (
        <SourceSkeleton />
      ) : view.isError ? (
        <ErrorBlock error={view.error} title={t("sources.deployKey.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      ) : repository === null ? (
        <Notice title={t("sources.notGit.title")}>{t("sources.notGit.body")}</Notice>
      ) : (
        <>
          <DeployKeyCard domain={domain} repository={repository} />
          <GitCredentialCard domain={domain} repository={repository} />
          <p className="max-w-measure text-13 text-pretty text-fg-muted">
            {t.rich("sources.allKeys", {
              link: (
                <TextLink key="integrations" to="/settings/integrations" size="inline">
                  {t("sources.allKeysLink")}
                </TextLink>
              ),
            })}
          </p>
          <p className="max-w-measure text-13 text-pretty text-fg-muted">
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
      <CommandHint command={`noust app deploy-key show ${domain}`} label={t("sources.fromTerminal")} />
    </Section>
  );
}
