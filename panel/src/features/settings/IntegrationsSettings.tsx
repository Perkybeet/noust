import { useQuery } from "@tanstack/react-query";

import { configQuery } from "../../api/queries/config";
import { githubStatusQuery } from "../../api/queries/github";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import { QueryState } from "../../components/page/QueryState";
import { Sections } from "../../components/page/Section";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { ReadOnlyLine } from "../../components/page/permissions";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { useHasFleet } from "../../nodes/servers";
import { useNode } from "../../nodes/useNode";
import { GitHubIntegration } from "./github/GitHubIntegration";
import { GitLabIntegration } from "./gitlab/GitLabIntegration";
import { readServerIdentity } from "./GeneralSettings.model";
import { AllowedSourcesSection } from "./sources/AllowedSourcesSection";
import { SourcesSection } from "./sources/SourcesSection";

/**
 * Where GitHub, GitLab, Gitea and Bitbucket deliver pushes: the public hooks address every integration
 * needs first. Written by `noust web expose-hooks`, which also serves it; only read here, and pasted into
 * the code host, so it is a pair with its copy button always in view.
 */
function HooksAddress() {
  const t = useT();
  const query = useQuery(configQuery());
  return (
    <SurfaceSection title={t("settings.integrations.hooks.title")} icon={OBJECT_ICONS.webhook} description={t("settings.integrations.hooks.description")}>
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
              {/* The console cannot put a domain in front of this server: the terminal of the machine does. */}
              <CommandHint command="noust web expose-hooks hooks.example.com" reason="solo-terminal" />
            </div>
          ) : (
            <KeyValueList items={[{ label: t("settings.integrations.hooks.addressLabel"), value: url, pasted: true }]} />
          );
        }}
      </QueryState>
    </SurfaceSection>
  );
}

/** GitLab, Gitea and Bitbucket: no App, a webhook per application, set where the application is. */
function OtherHosts() {
  const t = useT();
  return (
    <SurfaceSection
      title={t("settings.integrations.others.title")}
      icon={OBJECT_ICONS.integration}
      description={t("settings.integrations.others.description")}
      actions={
        <TextLink to="/apps" size="ui">
          {t("settings.integrations.others.appsLink")}
        </TextLink>
      }
    />
  );
}

/**
 * Settings > Integrations: where code hosts reach this server, GitHub's App, GitLab, and the others. Four slabs, each
 * saying what it is: the address for webhooks as a pair to copy, GitHub as the App's card beside where it is
 * installed, GitLab as its connections or the first time, and the hosts that need nothing in one line with their
 * way. What the page said in fourteen blocks of text, and the commands that repeated its buttons, are gone; the one
 * command left is the terminal's alone.
 */
export function IntegrationsSettings() {
  const t = useT();
  useDocumentTitle(t("settings.integrations.documentTitle"), 1);
  // What follows GitHub arrives with it: its height depends on how far the App is set up, and
  // nothing already on screen should be pushed down when that is known.
  const github = useQuery(githubStatusQuery());
  const config = useQuery(configQuery());
  const settled = (github.data !== undefined || github.isError) && (config.data !== undefined || config.isError);
  // What a central adds: how its servers reach their repositories. A server's own page has no fleet to say it of.
  const { node } = useNode();
  const hasFleet = useHasFleet();
  return (
    <Sections surfaces>
      <ReadOnlyLine permission="apps.manage" />
      <HooksAddress />
      <GitHubIntegration />
      {hasFleet && node === null ? <SourcesSection /> : null}
      {settled ? (
        <>
          <GitLabIntegration />
          <OtherHosts />
          <AllowedSourcesSection />
        </>
      ) : null}
    </Sections>
  );
}
