import { useQuery } from "@tanstack/react-query";

import { complianceQuery } from "../../../api/queries/compliance";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { useCan } from "../../variables/useCan";
import { ControlsSection } from "./ControlsSection";
import { FrameworksSection } from "./FrameworksSection";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

/**
 * Settings > Security > Compliance, in two parts: the frameworks that can be switched on (each
 * with what it turns on and the clause each control serves, shown before anything is confirmed),
 * and the controls they are made of, each showing which enabled framework locks it. Nothing is
 * on until it is chosen. The words never say a server complies: a framework turns on technical
 * controls, and the rest is the organisation's.
 */
export function ComplianceSection() {
  const t = useT();
  const query = useQuery(complianceQuery());
  const canManage = useCan("security.manage");
  const state = query.data;
  return (
    <Section title={t("complianceSetup.title")} icon={OBJECT_ICONS.control} description={t("complianceSetup.description")} loading={state === undefined && !query.isError}>
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("complianceSetup.honest")}</p>
      {query.isError && state === undefined ? (
        <ErrorBlock error={query.error} title={t("complianceSetup.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      ) : state === undefined ? (
        <LoadingRegion label={t("complianceSetup.loading")} className="flex flex-col gap-3">
          <Skeleton className="h-28 w-full rounded-card" />
          <Skeleton className="h-28 w-full rounded-card" />
          <Skeleton className="h-28 w-full rounded-card" />
        </LoadingRegion>
      ) : (
        <div className="flex flex-col gap-8">
          <FrameworksSection state={state} canManage={canManage} />
          <ControlsSection state={state} canManage={canManage} />
        </div>
      )}
    </Section>
  );
}

export default ComplianceSection;
