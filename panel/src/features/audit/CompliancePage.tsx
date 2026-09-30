import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";
import { useState } from "react";

import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section, Sections } from "../../components/page/Section";
import { Subsection } from "../../components/page/Subsection";
import { Button, buttonClassName } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import type { Status } from "../../components/ui/StatusPill";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { profileLabel } from "../settings/SecuritySettings";
import { checkQuery, incidentQuery, trailKeys } from "./api";
import type { EnsFinding } from "./api";

/** A finding's state: failing, a warning, met, or not applicable here. */
export function findingView(t: T, status: string): { status: Status; label: string } {
  switch (status) {
    case "fail":
      return { status: "failed", label: t("compliance.status.fail") };
    case "warning":
      return { status: "warning", label: t("compliance.status.warning") };
    case "ok":
      return { status: "running", label: t("compliance.status.ok") };
    default:
      return { status: "unknown", label: t("compliance.status.na") };
  }
}

const RANK: Record<string, number> = { fail: 0, warning: 1, ok: 2, "n/a": 3 };

/** The measure family a code belongs to: `op.acc.6.r8` is `op.acc`, `org.1.3` is `org`. */
export function familyOf(code: string): string {
  const parts = code.split(".");
  return parts[0] === "org" ? "org" : parts.slice(0, 2).join(".");
}

function familyName(t: T, family: string): string | null {
  switch (family) {
    case "org":
      return t("compliance.families.org");
    case "op.pl":
      return t("compliance.families.opPl");
    case "op.acc":
      return t("compliance.families.opAcc");
    case "op.exp":
      return t("compliance.families.opExp");
    case "op.ext":
      return t("compliance.families.opExt");
    case "op.cont":
      return t("compliance.families.opCont");
    case "op.mon":
      return t("compliance.families.opMon");
    case "mp.eq":
      return t("compliance.families.mpEq");
    case "mp.com":
      return t("compliance.families.mpCom");
    case "mp.si":
      return t("compliance.families.mpSi");
    case "mp.sw":
      return t("compliance.families.mpSw");
    case "mp.info":
      return t("compliance.families.mpInfo");
    case "mp.s":
      return t("compliance.families.mpS");
    default:
      return null;
  }
}

export interface MeasureGroup {
  family: string;
  findings: EnsFinding[];
  worst: string;
}

/**
 * Findings by the family of their first measure, worst first within each and the worst groups
 * first: what fails is what the operator reads first. A finding answering several measures
 * lists the others under it.
 */
export function groupFindings(findings: readonly EnsFinding[]): MeasureGroup[] {
  const groups = new Map<string, EnsFinding[]>();
  for (const finding of findings) {
    const family = familyOf(finding.measures[0] ?? "");
    const list = groups.get(family) ?? [];
    list.push(finding);
    groups.set(family, list);
  }
  return [...groups.entries()]
    .map(([family, list]) => {
      const sorted = [...list].sort((a, b) => (RANK[a.status] ?? 9) - (RANK[b.status] ?? 9) || a.id.localeCompare(b.id));
      return { family, findings: sorted, worst: sorted[0]?.status ?? "n/a" };
    })
    .sort((a, b) => (RANK[a.worst] ?? 9) - (RANK[b.worst] ?? 9) || a.family.localeCompare(b.family));
}

function FindingState({ t, status }: { t: T; status: string }) {
  const view = findingView(t, status);
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 text-13 text-fg">
      <StatusGlyph state={view.status} className={stateTextClass(view.status)} />
      {view.label}
    </span>
  );
}

function Finding({ t, finding }: { t: T; finding: EnsFinding }) {
  const [open, setOpen] = useState(false);
  const evidence = finding.evidence ?? [];
  return (
    <li className="flex flex-col gap-2 border-t border-border px-5 py-4 first:border-t-0">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="text-14 font-medium text-fg">{finding.title}</p>
          <p className="text-13 text-pretty text-fg-muted">
            <Mono tone="muted">{finding.id}</Mono> · <Mono tone="muted">{finding.measures.join(", ")}</Mono>
          </p>
        </div>
        <FindingState t={t} status={finding.status} />
      </div>
      <p className="max-w-measure text-13 text-pretty text-fg">{finding.summary}</p>
      {finding.remediation !== "" && finding.status !== "ok" ? (
        <p className="max-w-measure text-13 text-pretty text-fg">
          <span className="font-medium">{t("compliance.fixLabel")}</span> {finding.remediation}
        </p>
      ) : null}
      {evidence.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          <Button size="sm" variant="ghost" className="self-start" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
            {open ? t("compliance.hideEvidence") : t("compliance.showEvidence", { count: evidence.length })}
          </Button>
          {open ? (
            <SystemOutput label={t("compliance.evidenceLabel", { id: finding.id })} maxHeight="max-h-60">
              {evidence.join("\n")}
            </SystemOutput>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

/**
 * One family of measures: what fails or needs attention in full, what is met folded under one
 * line, so the page reads as the list of things to do.
 */
function MeasureCard({ t, group }: { t: T; group: MeasureGroup }) {
  const [showMet, setShowMet] = useState(false);
  const name = familyName(t, group.family);
  const worst = findingView(t, group.worst).status;
  const open = group.findings.filter((finding) => finding.status === "fail" || finding.status === "warning");
  const settled = group.findings.filter((finding) => finding.status !== "fail" && finding.status !== "warning");
  const shown = showMet ? group.findings : open;
  return (
    <Card
      padding="none"
      title={
        <span className="inline-flex items-center gap-2">
          <StatusGlyph state={worst} className={stateTextClass(worst)} />
          <Mono tone="default">{group.family}</Mono>
          {name !== null ? <span>{name}</span> : null}
        </span>
      }
      description={t("compliance.groupCount", { count: group.findings.length })}
      {...(settled.length > 0
        ? {
            actions: (
              <Button size="sm" variant="ghost" aria-expanded={showMet} onClick={() => setShowMet((value) => !value)}>
                {showMet ? t("compliance.hideMet") : t("compliance.showMet", { count: settled.length })}
              </Button>
            ),
          }
        : {})}
    >
      {shown.length > 0 ? (
        <ul>
          {shown.map((finding) => (
            <Finding key={finding.id} t={t} finding={finding} />
          ))}
        </ul>
      ) : undefined}
    </Card>
  );
}

/** Whether the console is locked down for an incident: said first, when it is. */
export function LockdownNotice() {
  const t = useT();
  const query = useQuery(incidentQuery());
  const lockdown = query.data;
  if (lockdown?.locked !== true) return null;
  return (
    <Notice tone="error" variant="banner" title={t("compliance.lockdown.title")}>
      <div className="flex flex-col gap-2">
        <p className="max-w-measure">{t("compliance.lockdown.description")}</p>
        <KeyValueList
          items={[
            ...(lockdown.since ? [{ label: t("compliance.lockdown.since"), value: <RelativeTime value={lockdown.since} />, mono: false, copy: false as const }] : []),
            ...(lockdown.by ? [{ label: t("compliance.lockdown.by"), value: lockdown.by }] : []),
            ...(lockdown.reason ? [{ label: t("compliance.lockdown.reason"), value: lockdown.reason, mono: false }] : []),
            ...(lockdown.package ? [{ label: t("compliance.lockdown.package"), value: lockdown.package }] : []),
          ]}
        />
        <CommandHint label={t("compliance.lockdown.command")} command="noust incident unfreeze" />
      </div>
    </Notice>
  );
}

/**
 * Settings > Compliance (the central's): this server against the ENS category MEDIUM profile,
 * check by check, grouped by the measure each answers, with what was found, the evidence
 * verbatim and the fix; and the evidence report for an auditor, to download.
 */
export function CompliancePage() {
  const t = useT();
  useDocumentTitle(t("compliance.page.documentTitle"), 1);
  const queryClient = useQueryClient();
  const [refresh, setRefresh] = useState(false);
  const query = useQuery(checkQuery(refresh));
  const result = query.data;
  const groups = result !== undefined ? groupFindings(result.findings) : [];

  const verdictTone = result?.verdict === "fail" ? "error" : result?.verdict === "warning" ? "warning" : "success";
  const counts = result?.counts ?? {};
  const errors = result?.errors ?? {};
  const indicators = result?.indicators ?? {};

  return (
    <Sections>
      <LockdownNotice />
      <Section
        title={t("compliance.page.title")}
        description={t("compliance.page.description")}
        actions={
          <>
            <Button
              icon={<RefreshCw aria-hidden="true" />}
              loading={query.isFetching && refresh}
              onClick={() => {
                if (refresh) void queryClient.invalidateQueries({ queryKey: trailKeys.check });
                setRefresh(true);
              }}
            >
              {t("compliance.page.checkAgain")}
            </Button>
            <a href="/api/ens/report?format=markdown" download className={buttonClassName("primary", "md")}>
              <Download aria-hidden="true" className="size-icon-md" />
              {t("compliance.page.download")}
            </a>
          </>
        }
      >
        {query.isError && result === undefined ? (
          <ErrorBlock error={query.error} title={t("compliance.page.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
        ) : result === undefined ? (
          <div aria-busy="true" className="flex flex-col gap-4">
            <span className="sr-only">{t("compliance.page.loading")}</span>
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : (
          <div className="flex flex-col gap-6">
            <Notice
              tone={verdictTone}
              title={
                result.verdict === "fail"
                  ? t("compliance.verdict.fail", { count: counts["fail"] ?? 0 })
                  : result.verdict === "warning"
                    ? t("compliance.verdict.warning", { count: counts["warning"] ?? 0 })
                    : t("compliance.verdict.ok")
              }
            >
              {t.rich("compliance.verdict.summary", {
                profile: profileLabel(t, result.profile),
                ok: String(counts["ok"] ?? 0),
                warning: String(counts["warning"] ?? 0),
                fail: String(counts["fail"] ?? 0),
                when: <RelativeTime key="when" value={result.checked_at} />,
              })}
            </Notice>
            {Object.keys(errors).length > 0 ? (
              <Notice tone="warning" title={t("compliance.page.partialTitle")}>
                <div className="flex flex-col gap-2">
                  <p>{t("compliance.page.partial")}</p>
                  <SystemOutput label={t("compliance.page.partialLabel")}>
                    {Object.entries(errors)
                      .map(([source, error]) => `${source}: ${error}`)
                      .join("\n")}
                  </SystemOutput>
                </div>
              </Notice>
            ) : null}
            {groups.map((group) => (
              <MeasureCard key={group.family} t={t} group={group} />
            ))}
            {Object.keys(indicators).length > 0 ? (
              <Subsection title={t("compliance.indicators.title")} description={t("compliance.indicators.description")}>
                <SystemOutput label={t("compliance.indicators.title")} maxHeight="max-h-80">
                  {JSON.stringify(indicators, null, 2)}
                </SystemOutput>
              </Subsection>
            ) : null}
          </div>
        )}
      </Section>
      <CommandHint label={t("compliance.page.fromTerminal")} command="noust ens check && noust ens report" />
    </Sections>
  );
}
