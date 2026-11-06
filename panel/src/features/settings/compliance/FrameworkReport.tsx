import type { ComplianceFinding, ComplianceFramework, ComplianceVerdict } from "../../../api/queries/compliance";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Can } from "../../../components/page/permissions";
import { Button } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { HelpPopover } from "../../../components/ui/HelpPopover";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusGlyph, StatusPill, stateTextClass } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { MANAGE } from "./FrameworkCards";
import { FIX_LINKS, controlTitle, findingsToMend } from "./model";

/** A finding's state: failing, a warning, met, or not applicable here. */
export function findingView(t: T, status: string): { status: Status; label: string } {
  switch (status) {
    case "fail":
      return { status: "failed", label: t("complianceSetup.report.status.fail") };
    case "warning":
      return { status: "warning", label: t("complianceSetup.report.status.warning") };
    case "ok":
      return { status: "running", label: t("complianceSetup.report.status.ok") };
    default:
      return { status: "unknown", label: t("complianceSetup.report.status.na") };
  }
}

/** `op.acc.5: second factor on every account` is `op.acc.5`: the number a person looks up. */
function clauseId(clause: string): string {
  const colon = clause.indexOf(": ");
  return colon === -1 ? clause : clause.slice(0, colon);
}

function columnsFor(t: T): Column<ComplianceFinding>[] {
  return [
    {
      id: "control",
      header: t("complianceSetup.report.column.control"),
      card: "title",
      cell: (finding) => (
        <span className="flex min-w-0 flex-col">
          <span className="text-13 text-fg">{controlTitle(t, finding.control)}</span>
          {/* The clause is its number: the sentence after it is the framework's, and the control's own name says what it is. */}
          {finding.clauses.length > 0 ? <Mono tone="muted">{finding.clauses.map(clauseId).join(", ")}</Mono> : null}
        </span>
      ),
    },
    {
      id: "state",
      header: t("complianceSetup.report.column.state"),
      card: "status",
      width: "w-44",
      cell: (finding) => {
        const view = findingView(t, finding.status);
        return (
          <span className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-13 text-fg">
            <StatusGlyph state={view.status} className={stateTextClass(view.status)} />
            {view.label}
            {/* What the server found and how to put it right, as it wrote them: the console does not reword a finding. */}
            <HelpPopover label={t("complianceSetup.report.moreLabel", { name: controlTitle(t, finding.control) })} title={controlTitle(t, finding.control)}>
              <div className="flex flex-col gap-2 text-13 text-pretty">
                <p>{finding.summary}</p>
                {finding.remediation !== "" ? (
                  <p>
                    <span className="font-medium">{t("complianceSetup.report.fix")}</span> {finding.remediation}
                  </p>
                ) : null}
              </div>
            </HelpPopover>
          </span>
        );
      },
    },
    {
      id: "fix",
      header: t("complianceSetup.report.column.fix"),
      card: "meta",
      width: "w-44",
      align: "end",
      cell: (finding) => {
        const link = FIX_LINKS[finding.control];
        return link === undefined ? null : (
          <TextLink to={link.to} size="ui">
            {t(link.label)}
          </TextLink>
        );
      },
    },
  ];
}

export interface FrameworkReportProps {
  framework: ComplianceFramework;
  /** The check's answer for it; undefined while it is read, or for a role that may not read the report. */
  verdict: ComplianceVerdict | undefined;
  /** The check is being read. */
  reading: boolean;
  /** The role may not read the report: said, so a section that is only its head is not a mystery. */
  forbidden: boolean;
  onTurnOff: (framework: ComplianceFramework) => void;
}

/**
 * A framework that is on, and how it stands: one line (how many of its controls are met), the ones that are not, each with
 * what it is, its state and the page where it is mended, and the way to turn it off. What is met is a count, not a list: the
 * document for an auditor lists them one by one. Words never say the server complies: a pass is a control in force.
 */
export function FrameworkReport({ framework, verdict, reading, forbidden, onTurnOff }: FrameworkReportProps) {
  const t = useT();
  const findings = verdict?.findings ?? [];
  const mend = findingsToMend(findings);
  const met = findings.length - mend.length;
  const state: Status = mend.some((finding) => finding.status === "fail") ? "failed" : mend.length > 0 ? "warning" : "running";
  const pill =
    verdict === undefined ? undefined : (
      <StatusPill
        state={state}
        size="sm"
        label={mend.length === 0 ? t("complianceSetup.report.allMet", { count: findings.length }) : t("complianceSetup.report.someMet", { met, total: findings.length })}
      />
    );
  return (
    <SurfaceSection
      title={framework.name}
      icon={OBJECT_ICONS.control}
      level={2}
      {...(pill !== undefined ? { status: pill } : {})}
      description={t(framework.source === "profile" ? "complianceSetup.report.viaProfile" : "complianceSetup.report.line")}
      actions={
        <Can permission={MANAGE}>
          <Button
            size="sm"
            onClick={() => {
              onTurnOff(framework);
            }}
          >
            {t("complianceSetup.frameworks.disable")}
          </Button>
        </Can>
      }
      loading={reading}
      flush={verdict !== undefined && mend.length > 0}
    >
      {verdict === undefined ? (
        forbidden ? (
          <EmptyState variant="inline" title={t("complianceSetup.report.forbidden")} />
        ) : (
          <LoadingRegion label={t("complianceSetup.report.loading")} className="flex flex-col gap-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
          </LoadingRegion>
        )
      ) : mend.length === 0 ? (
        <EmptyState variant="inline" title={t("complianceSetup.report.nothingToMend")} />
      ) : (
        <>
          <DataTable caption={t("complianceSetup.report.caption", { name: framework.name })} columns={columnsFor(t)} rows={mend} getRowId={(finding) => finding.control} embedded />
          <p className="border-t border-border px-5 py-3 text-13 text-fg-muted">{t("complianceSetup.report.met", { count: met })}</p>
        </>
      )}
    </SurfaceSection>
  );
}
