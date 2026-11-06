import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";

import { COMPLIANCE_REPORT_URL, complianceCheckQuery, complianceQuery } from "../../../api/queries/compliance";
import type { ComplianceFramework } from "../../../api/queries/compliance";
import { useDocumentTitle } from "../../../app/documentTitle";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section, Sections } from "../../../components/page/Section";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { ReadOnlyLine, useCan } from "../../../components/page/permissions";
import { buttonClassName } from "../../../components/ui/Button";
import { Disclosure } from "../../../components/ui/Disclosure";
import { HelpPopover } from "../../../components/ui/HelpPopover";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { LockdownNotice } from "../../audit/LockdownNotice";
import { SettingsMasterSwitch, SettingsPrimaryAction } from "../SettingsPrimaryAction";
import { ChangeDialog } from "./ChangeDialog";
import { ControlsSection } from "./ControlsSection";
import { FrameworkCards, MANAGE } from "./FrameworkCards";
import { FrameworkReport } from "./FrameworkReport";

/** What stays the organisation's and what the console does not enforce, said before a framework is turned on: it is the honest half of the answer. */
function WhatStaysYours({ framework }: { framework: ComplianceFramework }) {
  const t = useT();
  if (framework.organisational.length === 0 && framework.not_covered.length === 0) return null;
  return (
    <Disclosure label={t("complianceSetup.frameworks.staysYours")}>
      <div className="flex flex-col gap-3">
        {framework.organisational.length > 0 ? (
          <div className="flex flex-col gap-1">
            <p className="text-12 font-medium text-fg">{t("complianceSetup.frameworks.organisational")}</p>
            <ul className="flex list-disc flex-col gap-1 pl-4 text-12 text-fg-muted">
              {framework.organisational.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {framework.not_covered.length > 0 ? (
          <div className="flex flex-col gap-1">
            <p className="text-12 font-medium text-fg">{t("complianceSetup.frameworks.notCovered")}</p>
            <ul className="flex list-disc flex-col gap-1 pl-4 text-12 text-fg-muted">
              {framework.not_covered.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Disclosure>
  );
}

/**
 * Settings > Compliance: the rule books Noust can hold itself to (ENS, ISO 27001, NIS2, CIS, SOC 2, GDPR: the server's list,
 * not one the console wrote), what each turns on, and how the ones that are on stand. With none on, which is the usual state
 * of a server that nothing obliges, the page says so as a fact and not as a warning, and there is no report to be red:
 * the frameworks are cards to turn on, each asking first what would change. With one on, its report is the page: what is
 * not met, and where it is mended. The controls the frameworks are made of are below, and the ones about signing in are
 * Access security's. A framework turns on technical controls; it does not make a server compliant, and no word here says so.
 */
export function ComplianceSettings() {
  const t = useT();
  useDocumentTitle(t("nav.settingsTabs.compliance.label"), 1);
  const query = useQuery(complianceQuery());
  const canRead = useCan("compliance.read");
  const canManage = useCan(MANAGE);
  const state = query.data;
  const active = state?.frameworks.filter((framework) => framework.enabled) ?? [];
  const off = state?.frameworks.filter((framework) => !framework.enabled) ?? [];
  const check = useQuery({ ...complianceCheckQuery(), enabled: canRead && active.length > 0 });
  const [asked, setAsked] = useState<{ framework: ComplianceFramework; enable: boolean } | null>(null);
  const names = new Map((state?.frameworks ?? []).map((framework) => [framework.id, framework.name]));

  return (
    <Sections surfaces>
      <LockdownNotice />
      <SettingsMasterSwitch>
        <HelpPopover label={t("complianceSetup.helpLabel")} title={t("nav.settingsTabs.compliance.label")}>
          <div className="flex flex-col gap-2 text-13 text-pretty text-fg-muted">
            <p>{t("complianceSetup.description")}</p>
            <p>{t("complianceSetup.honest")}</p>
          </div>
        </HelpPopover>
      </SettingsMasterSwitch>
      {active.length > 0 && canRead ? (
        <SettingsPrimaryAction>
          {/* The report is a document to hand over: the browser saves what the server wrote, with its hash in a header. */}
          <a href={COMPLIANCE_REPORT_URL} download className={buttonClassName("primary", "md")}>
            <Download aria-hidden="true" className="size-icon-md" />
            {t("complianceSetup.report.download")}
          </a>
        </SettingsPrimaryAction>
      ) : null}
      <ReadOnlyLine permission={MANAGE} />
      {query.isError && state === undefined ? (
        <ErrorBlock error={query.error} title={t("complianceSetup.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      ) : state === undefined ? (
        <LoadingRegion label={t("complianceSetup.loading")} className="flex flex-col gap-6">
          <Skeleton className="h-20 w-full rounded-card" />
          <Skeleton className="h-40 w-full rounded-card" />
        </LoadingRegion>
      ) : (
        <>
          {active.length === 0 ? <SurfaceSection title={t("complianceSetup.none.title")} icon={OBJECT_ICONS.control} description={t("complianceSetup.none.description")} /> : null}
          {active.map((framework) => (
            <FrameworkReport
              key={framework.id}
              framework={framework}
              verdict={check.data?.frameworks.find((verdict) => verdict.framework === framework.id)}
              reading={canRead && check.isPending}
              forbidden={!canRead}
              onTurnOff={(chosen) => {
                setAsked({ framework: chosen, enable: false });
              }}
            />
          ))}
          {check.isError && canRead && active.length > 0 ? <ErrorBlock compact error={check.error} title={t("complianceSetup.report.loadFailed")} onRetry={() => void check.refetch()} retrying={check.isRefetching} /> : null}
          {off.length > 0 ? (
            <Section title={active.length === 0 ? t("complianceSetup.frameworks.title") : t("complianceSetup.frameworks.others")} description={active.length === 0 ? t("complianceSetup.frameworks.description") : undefined}>
              <FrameworkCards
                frameworks={off}
                onTurnOn={(chosen) => {
                  setAsked({ framework: chosen, enable: true });
                }}
              />
            </Section>
          ) : null}
          <ControlsSection state={state} canManage={canManage} />
        </>
      )}
      {asked !== null && state !== undefined ? (
        <ChangeDialog
          request={asked.enable ? { enable: [asked.framework.id] } : { disable: [asked.framework.id] }}
          title={t(asked.enable ? "complianceSetup.dialog.enableTitle" : "complianceSetup.dialog.disableTitle", { name: asked.framework.name })}
          lead={asked.enable ? t("complianceSetup.dialog.enableLead") : t("complianceSetup.dialog.disableLead")}
          action={asked.enable ? t("complianceSetup.dialog.enableAction") : t("complianceSetup.dialog.disableAction")}
          loosens={!asked.enable}
          done={t(asked.enable ? "complianceSetup.dialog.turnedOn" : "complianceSetup.dialog.turnedOff", { name: asked.framework.name })}
          controls={state.controls}
          names={names}
          {...(asked.enable ? { extra: <WhatStaysYours framework={asked.framework} /> } : {})}
          onClose={() => {
            setAsked(null);
          }}
        />
      ) : null}
    </Sections>
  );
}
