import { useQuery } from "@tanstack/react-query";

import { configQuery } from "../../api/queries/config";
import { githubStatusQuery } from "../../api/queries/github";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { QueryState } from "../../components/page/QueryState";
import { Section, Sections } from "../../components/page/Section";
import { Card } from "../../components/ui/Card";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { GitHubIntegration } from "./github/GitHubIntegration";
import { readServerIdentity } from "./GeneralSettings.model";

/**
 * Where GitHub, GitLab and Gitea deliver pushes: the public hooks address every integration
 * needs first. Written by `noust web expose-hooks`, which also serves it; only read here.
 */
function HooksAddress() {
  const t = useT();
  const query = useQuery(configQuery());
  return (
    <Section title={t("settings.integrations.hooks.title")} description={t("settings.integrations.hooks.description")}>
      <Card>
        <QueryState
          query={query}
          label={t("settings.integrations.hooks.loadingLabel")}
          skeleton={
            <div className="flex flex-col gap-2">
              <Skeleton className="h-5 w-72 max-w-full" />
              <Skeleton className="h-4 w-96 max-w-full" />
            </div>
          }
        >
          {(data) => {
            const url = readServerIdentity(data.config).hooksUrl;
            return url === "" ? (
              <div className="flex min-w-0 flex-col gap-3">
                <p className="flex items-center gap-2 text-13 font-medium text-fg">
                  <StatusGlyph state="stopped" className="text-idle" />
                  {t("settings.integrations.hooks.unsetTitle")}
                </p>
                <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("settings.integrations.hooks.unsetDescription")}</p>
                <CommandHint command="noust web expose-hooks hooks.example.com" />
              </div>
            ) : (
              <div className="flex min-w-0 flex-col gap-1">
                <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-13 text-fg">
                  {t.rich("settings.integrations.hooks.set", { url: <Mono key="url">{url}</Mono> })}
                </p>
                <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("settings.integrations.hooks.setDescription")}</p>
              </div>
            );
          }}
        </QueryState>
      </Card>
    </Section>
  );
}

/** GitLab and Gitea: no App, a webhook per application, set where the application is. */
function OtherHosts() {
  const t = useT();
  return (
    <Section title={t("settings.integrations.others.title")} description={t("settings.integrations.others.description")}>
      <Card>
        <p className="max-w-measure text-13 text-pretty text-fg-muted">
          {t.rich("settings.integrations.others.body", {
            apps: (
              <TextLink key="apps" to="/apps">
                {t("settings.integrations.others.appsLink")}
              </TextLink>
            ),
          })}
        </p>
      </Card>
    </Section>
  );
}

/** Settings > Integrations: where code hosts reach this server, GitHub's App, and the others. */
export function IntegrationsSettings() {
  const t = useT();
  useDocumentTitle(t("settings.integrations.documentTitle"), 1);
  // What follows GitHub arrives with it: its height depends on how far the App is set up, and
  // nothing already on screen should be pushed down when that is known.
  const github = useQuery(githubStatusQuery());
  const config = useQuery(configQuery());
  const settled = (github.data !== undefined || github.isError) && (config.data !== undefined || config.isError);
  return (
    <Sections>
      <HooksAddress />
      <GitHubIntegration />
      {settled ? (
        <>
          <OtherHosts />
          <CommandHint command="noust github status" label={t("settings.integrations.fromTerminal")} />
        </>
      ) : null}
    </Sections>
  );
}
