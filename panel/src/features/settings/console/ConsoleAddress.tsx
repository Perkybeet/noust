import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { configQuery } from "../../../api/queries/config";
import { useDocumentTitle } from "../../../app/documentTitle";
import { KeyValueList, KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import { QueryState } from "../../../components/page/QueryState";
import { Sections } from "../../../components/page/Section";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { buttonClassName } from "../../../components/ui/Button";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import { FleetBlock } from "../fleet/FleetBlock";
import { FleetStrip } from "../fleet/FollowLine";
import { readServerIdentity } from "../GeneralSettings.model";

/**
 * Settings > Console address: the address the links of the notices open (item 166). What the central carries to every
 * server is the address of its own console, with each server adding its name to it, and it is said here with the servers
 * that follow it. Publishing the console of a machine on a name of its own over HTTPS is the machine's, a page of Server.
 */
export function ConsoleAddress() {
  const t = useT();
  useDocumentTitle(t("consoleAccess.address.documentTitle"), 1);
  const config = useQuery(configQuery());
  return (
    <Sections>
      <FleetStrip />
      <SurfaceSection title={t("consoleAccess.address.thisTitle")} icon={OBJECT_ICONS.domain} description={t("consoleAccess.address.thisDescription")}>
        <QueryState query={config} label={t("consoleAccess.address.loading")} skeleton={<KeyValueListSkeleton rows={1} />}>
          {(data) => {
            const address = readServerIdentity(data.config).publicUrl;
            return <KeyValueList items={[{ label: t("consoleAccess.address.addressLabel"), value: address === "" ? null : address, pasted: true }]} empty={t("consoleAccess.address.unset")} />;
          }}
        </QueryState>
      </SurfaceSection>
      <FleetBlock resource="console_link" />
      <SurfaceSection
        title={t("consoleAccess.address.publishTitle")}
        icon={OBJECT_ICONS.console}
        description={t("consoleAccess.address.publishDescription")}
        actions={
          <Link to="/server/console" className={buttonClassName("secondary", "sm")}>
            {t("consoleAccess.address.publishAction")}
          </Link>
        }
      />
    </Sections>
  );
}
