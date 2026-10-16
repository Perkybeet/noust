import { useQuery } from "@tanstack/react-query";

import { complianceQuery } from "../../../api/queries/compliance";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { useCan } from "../../variables/useCan";
import { ControlsSection } from "./ControlsSection";
import { FrameworksSection } from "./FrameworksSection";

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
    <Section title={t("complianceSetup.title")} description={t("complianceSetup.description")} loading={state === undefined && !query.isError}>
      <Notice>{t("complianceSetup.honest")}</Notice>
      {query.isError && state === undefined ? (
        <ErrorBlock error={query.error} title={t("complianceSetup.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      ) : state === undefined ? (
        <div aria-busy="true" className="flex flex-col gap-3">
          <span className="sr-only">{t("complianceSetup.loading")}</span>
          <Skeleton className="h-28 w-full rounded-card" />
          <Skeleton className="h-28 w-full rounded-card" />
          <Skeleton className="h-28 w-full rounded-card" />
        </div>
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
