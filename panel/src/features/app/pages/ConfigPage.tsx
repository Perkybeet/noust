import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouterState } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import type { ComponentType } from "react";

import { appQuery } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { useDocumentTitle } from "../../../app/documentTitle";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Disclosure } from "../../../components/ui/Disclosure";
import { EmptyState } from "../../../components/ui/EmptyState";
import { SkeletonSection } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { APP_SCREENS } from "../../nav/app";
import { AccessSettings } from "../settings/AccessSettings";
import { AutomationSettings } from "../settings/AutomationSettings";
import { BuildSettings } from "../settings/BuildSettings";
import { ComposeStackSettings } from "../settings/ComposeStackSettings";
import { ContainerSettings } from "../settings/ContainerSettings";
import { DeployOnPush } from "../settings/DeployOnPush";
import { DeploysSettings } from "../settings/DeploysSettings";
import { DiskSettings } from "../settings/DiskSettings";
import { GeneralSettings } from "../settings/GeneralSettings";
import { HooksSettings } from "../settings/HooksSettings";
import { MountsSettings } from "../settings/MountsSettings";
import { PreviewsSettings } from "../settings/PreviewsSection";
import { ProcessesSettings } from "../settings/ProcessesSettings";
import { ProtectionSettings } from "../settings/ProtectionSettings";
import { ResourcesSettings } from "../settings/ResourcesSettings";
import { SecuritySettings } from "../settings/SecuritySettings";
import { SourceSettings } from "../settings/SourceSettings";
import { SaveHost } from "../settings/saveHost";
import { partsOf } from "../settings/sections";
import type { PagePart } from "../settings/sections";
import type { ConfigPageId } from "../settings/sections";
import { PartFoldedContext, PartHostContext, SettingsAppContext } from "../settings/settingsApp";

/** What each part is drawn with: the piece that was a subsection of the settings, with its own form and its own tests. */
const VIEWS: Readonly<Record<string, ComponentType>> = {
  source: SourceSettings,
  build: BuildSettings,
  container: ContainerSettings,
  deploys: DeploysSettings,
  compose: ComposeStackSettings,
  automation: AutomationSettings,
  "deploy-on-push": DeployOnPush,
  hooks: HooksSettings,
  previews: PreviewsSettings,
  general: GeneralSettings,
  resources: ResourcesSettings,
  processes: ProcessesSettings,
  mounts: MountsSettings,
  disk: DiskSettings,
  security: SecuritySettings,
  access: AccessSettings,
  protection: ProtectionSettings,
};

/**
 * The parts a page keeps folded under a button of their own, by page: what a person comes to the page for is open, and
 * what is set once and rarely looked at is one press away (the mockup's pages are a few surfaces, not every card of
 * what they gather). A folded part is still there, mounted, with its own form: its changes count on the page's bar.
 */
export const FOLDED: Readonly<Record<ConfigPageId, ReadonlySet<string>>> = {
  code: new Set(),
  // Releases and rollback is what the page is for; what deploys by itself, the push, the hooks and the previews are each one press away.
  triggers: new Set(["automation", "deploy-on-push", "hooks", "previews"]),
  resources: new Set(["processes", "mounts", "disk", "security"]),
  // Who may come in is the page; what the site limits and sends back is set once.
  access: new Set(["protection"]),
};

/** How long the page waits for what its parts read before it shows what it has. */
const REVEAL_LIMIT_MS = 400;
/** How long nothing may be on its way for the page to take it as done: a read that begins from another one's answer starts within it. */
const REVEAL_QUIET_MS = 60;

/**
 * Whether the page may show its parts. They are mounted at once, so each reads what it needs, but they are not shown while
 * anything is being read: a part that grows when its answer arrives would push the ones under it, and the page would shift
 * as it loaded, part after part. One shape stands in their place, then they all appear; a read that takes longer than a moment
 * is not waited for.
 */
function useRevealed(): boolean {
  const queryClient = useQueryClient();
  const [revealed, setRevealed] = useState(false);
  useEffect(() => {
    if (revealed) return;
    const reveal = (): void => {
      setRevealed(true);
    };
    let quiet: number | undefined;
    const check = (): void => {
      window.clearTimeout(quiet);
      quiet = window.setTimeout(() => {
        if (queryClient.isFetching() === 0) reveal();
      }, REVEAL_QUIET_MS);
    };
    const unsubscribe = queryClient.getQueryCache().subscribe(check);
    // The parts' own reads begin as they mount, so there is something to wait for (or nothing) by the time this runs.
    check();
    const limit = window.setTimeout(reveal, REVEAL_LIMIT_MS);
    return () => {
      unsubscribe();
      window.clearTimeout(quiet);
      window.clearTimeout(limit);
    };
  }, [queryClient, revealed]);
  return revealed;
}

/**
 * One of the four pages that gather what were the subsections of an application's settings (4.2, item 116):
 * the second column lists the page, and the page draws what it gathers, one titled card after another, saved
 * from one bar. The application is read once here and handed to each part, as the settings' layout did, so
 * none of them repeats the load, its skeleton or its failure (the app's own layout says those).
 */
export function ConfigPage({ page, domain }: { page: ConfigPageId; domain: string }) {
  const t = useT();
  const app = useQuery(appQuery(domain));
  const label = APP_SCREENS.find((screen) => screen.id === `app.${page}`)?.label;
  const section = label === undefined ? page : t(label);
  useDocumentTitle(t("appPages.config.documentTitle", { section, domain }), 1);

  if (app.data === undefined) {
    return app.isError ? null : (
      <LoadingRegion label={t("appPages.config.loading", { section })}>
        <SkeletonSection rows={5} />
      </LoadingRegion>
    );
  }
  const parts = partsOf(page, app.data);
  if (parts.length === 0) return <EmptyState variant="inline" title={t("appPages.config.notBuilt", { domain })} />;
  return <ConfigParts page={page} domain={domain} app={app.data} parts={parts} section={section} />;
}

/** The parts of a page, once the application is known: mounted at once, shown together (`useRevealed`). */
function ConfigParts({ page, domain, app, parts, section }: { page: ConfigPageId; domain: string; app: App; parts: readonly PagePart[]; section: string }) {
  const t = useT();
  const revealed = useRevealed();
  // A link that leads to a folded part names it (`#deploy-on-push`): that fold is open when the page is, and in view.
  const wanted = useRouterState({ select: (state) => state.location.hash });
  useEffect(() => {
    if (revealed && wanted !== "") document.getElementById(wanted)?.scrollIntoView({ block: "start" });
  }, [revealed, wanted]);
  return (
    <>
      {revealed ? null : (
        <LoadingRegion label={t("appPages.config.loading", { section })}>
          <SkeletonSection rows={5} />
        </LoadingRegion>
      )}
      <div hidden={!revealed}>
        <SettingsAppContext.Provider value={app}>
          <PartHostContext.Provider value>
            <SaveHost notSaved={t("appPages.config.notSaved", { domain })}>
              <div data-page={page} className="flex min-w-0 flex-col gap-6">
                {parts.map((part) => {
                  const View = VIEWS[part.id];
                  if (View === undefined) return null;
                  if (!FOLDED[page].has(part.id)) return <View key={part.id} />;
                  return (
                    <div key={part.id} id={part.id}>
                      <Disclosure variant="card" label={t(part.label)} defaultOpen={wanted === part.id}>
                        <PartFoldedContext.Provider value>
                          <View />
                        </PartFoldedContext.Provider>
                      </Disclosure>
                    </div>
                  );
                })}
              </div>
            </SaveHost>
          </PartHostContext.Provider>
        </SettingsAppContext.Provider>
      </div>
    </>
  );
}
