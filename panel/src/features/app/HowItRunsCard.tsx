import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { isApiError } from "../../api/client";
import { appRunsQuery } from "../../api/queries/apps";
import type { App } from "../../api/queries/apps";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { appStatus } from "../../components/page/status";
import { buttonClassName } from "../../components/ui/Button";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import type { T } from "../../i18n";
import { runsWords } from "./howItRuns";

/** The folder the service runs from: the active release on releases, the folder itself in place. */
function folderOf(app: App): string | null {
  if (app.path === null || app.path === undefined) return null;
  return app.layout === "releases" ? `${app.path}/current` : app.path;
}

/**
 * How the application runs, on its page: a service of the system, a container (built from its Dockerfile, by Railpack or from
 * an image of a registry) or a Compose stack, with what runs it and the way to it. A second line says that changing it is
 * not something that is converted on the fly: it is deployed again and the domain is moved. It says what the server says
 * (`GET /api/apps/{domain}/runs`) and draws nothing before it answers, nor against a server that has no such route (a node of 4.2
 * under a 4.3 central): the page is what it was.
 */
export function HowItRunsCard({ app, t }: { app: App; t: T }) {
  const runs = useQuery(appRunsQuery(app.domain));
  const data = runs.data;
  if (data === undefined) {
    // A server without the route (a node of 4.2) has nothing to say, and always had none; any other failure is said.
    if (runs.isError && !(isApiError(runs.error) && runs.error.status === 404)) {
      return <ErrorBlock compact error={runs.error} title={t("appPages.overview.howLoadError")} onRetry={() => void runs.refetch()} retrying={runs.isRefetching} />;
    }
    return null;
  }
  const staticSite = appStatus(app.status).state === "static";
  const words = runsWords(data, staticSite);
  const server = data.form === "server";
  const service = app.unit ? app.unit.replace(/\.service$/, "") : null;
  const folder = folderOf(app);
  const items: KeyValueItem[] = [{ label: t("appPages.overview.howForm"), value: t(words.form), mono: false, copy: false }];
  if (server) {
    if (app.unit) items.push({ label: t("appPages.overview.howService"), value: app.unit });
    if (app.run_as) items.push({ label: t("appPages.overview.howAccount"), value: app.run_as });
    if (folder !== null) items.push({ label: t("appPages.overview.howFolder"), value: folder });
  } else if (data.image) {
    items.push({ label: t("appPages.overview.howImage"), value: data.image });
  }
  return (
    <SurfaceSection
      title={t("appPages.overview.howTitle")}
      icon={server ? OBJECT_ICONS.service : OBJECT_ICONS.container}
      description={t(words.line)}
      actions={
        // A container's own page is the Docker zone's, which links here from its side when it exists: a link to a
        // page that is not there yet would be a dead end.
        server && service !== null ? (
          <Link to="/services/$name" params={{ name: service }} className={buttonClassName("secondary", "sm")}>
            {t("appPages.overview.howSeeService")}
          </Link>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-4">
        <KeyValueList items={items} />
        <p className="max-w-measure text-12 text-pretty text-fg-muted">{t(server ? "appPages.overview.howToContainer" : "appPages.overview.howToServer")}</p>
      </div>
    </SurfaceSection>
  );
}
