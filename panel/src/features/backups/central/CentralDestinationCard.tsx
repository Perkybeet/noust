import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { centralCopiesQuery, collectingStatusQuery } from "../../../api/queries/centralBackups";
import { ErrorBlock } from "../../../components/page/QueryState";
import { useCan } from "../../../components/page/permissions";
import { Setting } from "../../../components/ui/Setting";
import { SettingsGroup } from "../../../components/page/SettingsGroup";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { useHasFleet } from "../../../nodes/servers";
import { useNode } from "../../../nodes/useNode";
import { CollectSettingsDrawer } from "./CollectSettingsDrawer";
import { knobsOf } from "./central";
import { useCentralBackupActions } from "./useCentralBackupActions";

/**
 * "The central" as a place copies go to, among the destinations: the central comes for them through the tunnel it already
 * has to each server, so nothing is opened on the router and no server keeps the credentials of the place they are in. It is
 * only the central's own page of Backups that offers it: what a node answers for here is the node's, and a node is no central.
 * One switch turns the fleet's collecting on, the drawer has the rest and the copies are on their own page.
 */
export function CentralDestinationCard() {
  const hasFleet = useHasFleet();
  const { node } = useNode();
  if (!hasFleet || node !== null) return null;
  return <CentralCard />;
}

function CentralCard() {
  const t = useT();
  const canManage = useCan("backups.manage");
  const status = useQuery(collectingStatusQuery());
  const copies = useQuery(centralCopiesQuery());
  const { setEnabled } = useCentralBackupActions();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const knobs = knobsOf(status.data);
  const following = status.data?.following;

  // design-exception: hand-error-ternary the card is one line of a page that has other things to show: the failure replaces only it
  if (status.isError && status.data === undefined) {
    return <ErrorBlock error={status.error} title={t("centralBackups.card.loadFailed")} onRetry={() => void status.refetch()} retrying={status.isRefetching} />;
  }

  const on = knobs?.enabled === true || (following?.following ?? 0) > 0;
  const total = copies.data?.items.reduce((sum, copy) => sum + copy.size, 0) ?? 0;
  const count = copies.data?.total ?? 0;
  const help =
    following === undefined
      ? t("centralBackups.card.description")
      : following.following === 0
        ? t("centralBackups.following.none")
        : following.following === following.of && (following.exceptions ?? []).length === 0
          ? t("centralBackups.following.all")
          : t("centralBackups.following.some", { following: following.following, of: following.of });

  return (
    <>
      <SettingsGroup
        title={t("centralBackups.card.title")}
        icon={OBJECT_ICONS.central}
        description={t("centralBackups.card.description")}
        status={<StatusPill state={on ? "running" : "stopped"} label={on ? t("centralBackups.card.on") : t("centralBackups.card.off")} appearance="inline" size="sm" />}
        {...(canManage
          ? {
              actions: (
                <Button
                  size="sm"
                  onClick={() => {
                    setSettingsOpen(true);
                  }}
                >
                  {t("centralBackups.card.manage")}
                </Button>
              ),
            }
          : {})}
      >
        <Setting
          label={t("centralBackups.settings.enabled")}
          help={help}
          elevated
          checked={knobs?.enabled === true}
          busy={setEnabled.isPending}
          {...(canManage ? {} : { disabled: true as const, reason: t("centralBackups.card.noPermission") })}
          onCheckedChange={async (enabled) => {
            await setEnabled.mutateAsync(enabled);
          }}
        />
        <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 text-13">
          <span className="text-fg-muted tabular-nums">{count === 0 ? t("centralBackups.copies.empty") : t("centralBackups.card.held", { count, size: formatBytes(total, t.locale) })}</span>
          <Link to="/fleet/central-backups" className={buttonClassName("secondary", "sm")}>
            {t("centralBackups.card.see")}
          </Link>
        </div>
      </SettingsGroup>
      {settingsOpen ? <CollectSettingsDrawer knobs={knobs} open onOpenChange={setSettingsOpen} /> : null}
    </>
  );
}
