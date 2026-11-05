import { useQuery } from "@tanstack/react-query";

import { appQuery } from "../../../api/queries/apps";
import { useDocumentTitle } from "../../../app/documentTitle";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { SkeletonSection } from "../../../components/ui/Skeleton";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { useHasFleet } from "../../../nodes/servers";
import { isAdopted } from "../adopted";
import { ReplicationCard } from "../settings/ReplicationCard";

/**
 * Running the application on more than one server (item 120: with Export, it is a way of taking the application
 * elsewhere, not a danger). What decides whether a second server would serve the same thing is read here, on the
 * server it is on; where it is placed is the fleet's, one link away when there is a fleet.
 */
export function ServersPage({ domain }: { domain: string }) {
  const t = useT();
  const app = useQuery(appQuery(domain));
  const fleet = useHasFleet();
  useDocumentTitle(t("appPages.config.documentTitle", { section: t("nav.appTabs.servers.label"), domain }), 1);
  if (app.data === undefined) {
    return app.isError ? null : (
      <LoadingRegion label={t("appPages.config.loading", { section: t("nav.appTabs.servers.label") })}>
        <SkeletonSection rows={3} />
      </LoadingRegion>
    );
  }
  if (isAdopted(app.data)) return null;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <ReplicationCard domain={domain} />
      {fleet ? (
        <p className="text-13 text-fg-muted">
          {t.rich("appPages.servers.placements", {
            link: (
              <TextLink key="placements" to="/fleet/placements" size="inline">
                {t("appPages.servers.placementsLink")}
              </TextLink>
            ),
          })}
        </p>
      ) : null}
    </div>
  );
}
