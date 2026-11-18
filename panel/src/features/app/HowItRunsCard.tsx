import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { isApiError } from "../../api/client";
import { appRunsQuery } from "../../api/queries/apps";
import type { App } from "../../api/queries/apps";
import { KeyValueList, KeyValueListSkeleton } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { useCan } from "../../components/page/permissions";
import { ErrorBlock } from "../../components/page/QueryState";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { appStatus } from "../../components/page/status";
import { buttonClassName } from "../../components/ui/Button";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import type { T } from "../../i18n";
import { useScopeOffers } from "../../nodes/useNode";
import { formOfType } from "../new-app/howItRuns";
import { runsWords } from "./howItRuns";

/** The folder the service runs from: the active release on releases, the folder itself in place. */
function folderOf(app: App): string | null {
  if (app.path === null || app.path === undefined) return null;
  return app.layout === "releases" ? `${app.path}/current` : app.path;
}

/**
 * The card's place while the server's answer is on its way, with the rows and the line it will have: what the application says
 * of itself says which form it is, and so how many facts the form has. The card used to be drawn only when the answer came, a
 * moment after the page, and everything under it moved down by its height.
 */
function Holding({ app, t, otherWay }: { app: App; t: T; otherWay: boolean }) {
  const server = formOfType(app.app_type ?? "") === "server";
  const folder = folderOf(app);
  // The facts of a service of the system are its form, its unit, its account and its folder; a container's, its form and its image (a stack has none).
  const rows = server ? 1 + (app.unit ? 1 : 0) + (app.run_as ? 1 : 0) + (folder !== null ? 1 : 0) : app.app_type === "docker-compose" ? 1 : 2;
  return (
    <SurfaceSection
      title={t("appPages.overview.howTitle")}
      icon={server ? OBJECT_ICONS.service : OBJECT_ICONS.container}
      description={<Skeleton className="h-5 w-64 max-w-full" />}
      loading
    >
      <LoadingRegion label={t("appPages.overview.loadingHow")} className="flex flex-col gap-4">
        <KeyValueListSkeleton rows={rows} />
        {otherWay ? <Skeleton className="h-9 max-w-measure" /> : null}
      </LoadingRegion>
    </SurfaceSection>
  );
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
  const mayReadDocker = useCan("docker.read");
  // Offered by the register's one filter, like every link to a place: where the server has no Docker the link is not given.
  const offers = useScopeOffers();
  const mayOpenDocker = mayReadDocker && offers("docker_zone");
  // The other way is said only where the machine can do it (the same filter): one that only has Docker cannot run it directly.
  const canRunInContainer = offers("container_deploy");
  const canRunDirectly = offers("native_deploy");
  const data = runs.data;
  if (data === undefined) {
    // A server without the route (a node of 4.2) has nothing to say, and always had none; any other failure is said.
    if (runs.isError && !(isApiError(runs.error) && runs.error.status === 404)) {
      return <ErrorBlock compact error={runs.error} title={t("appPages.overview.howLoadError")} onRetry={() => void runs.refetch()} retrying={runs.isRefetching} />;
    }
    // Its place is held while the answer comes, in the shape it will have, so nothing under it moves when it does.
    if (runs.isPending) return <Holding app={app} t={t} otherWay={formOfType(app.app_type ?? "") === "server" ? canRunInContainer : canRunDirectly} />;
    return null;
  }
  const staticSite = appStatus(app.status).state === "static";
  const words = runsWords(data, staticSite);
  const server = data.form === "server";
  const otherWay = server ? canRunInContainer : canRunDirectly;
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
        // A container's own page is the Docker zone's: its Containers page, filtered to this application, for whoever may read Docker
        // and where the zone is offered (a link to a page the column does not have would be a dead end).
        server && service !== null ? (
          <Link to="/services/$name" params={{ name: service }} className={buttonClassName("secondary", "sm")}>
            {t("appPages.overview.howSeeService")}
          </Link>
        ) : mayOpenDocker ? (
          <Link to="/server/docker/containers" search={{ app: app.domain }} className={buttonClassName("secondary", "sm")}>
            {t("appPages.overview.howSeeDocker")}
          </Link>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-4">
        <KeyValueList items={items} />
        {/* The other way is said only where the machine can do it: a machine that only has Docker has no way to run it directly. */}
        {otherWay ? <p className="max-w-measure text-12 text-pretty text-fg-muted">{t(server ? "appPages.overview.howToContainer" : "appPages.overview.howToServer")}</p> : null}
      </div>
    </SurfaceSection>
  );
}
