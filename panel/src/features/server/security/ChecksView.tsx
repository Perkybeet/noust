/**
 * The hardening checks: the open findings first, each with what was found (and what the system
 * said, verbatim, a click away) and its way out; the accepted risks with who accepted them and
 * until when; the checks that passed folded into one line.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { request } from "../../../api/client";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatDate, parseTimestamp } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { ServerErrorBlock, explainServerError } from "../errors";
import { usePowerDialog } from "../PowerDialog";
import { checksQuery, serverKeys } from "../queries";
import type { SecurityCheck } from "../queries";
import { AcceptRiskDialog, FixDialog, GuidedDrawer } from "./FixDialogs";
import { actionTarget, checkState, checkTitle, isOpen, sortChecks } from "./data";

type Opened = { kind: "fix" | "guided" | "accept"; check: SecurityCheck } | null;

function Evidence({ check }: { check: SecurityCheck }) {
  const t = useT();
  if (check.evidence.length === 0) return null;
  return (
    <details className="group text-12">
      <summary className="w-fit cursor-pointer rounded-chip text-fg-muted hover:text-fg">{t("server.checks.evidence")}</summary>
      <SystemOutput label={t("server.checks.evidenceLabel", { check: checkTitle(t, check) })} maxHeight="max-h-40" className="mt-1.5 rounded-control bg-bg-sunken p-2.5">
        {check.evidence.join("\n")}
      </SystemOutput>
    </details>
  );
}

function FindingActions({ check, open }: { check: SecurityCheck; open: (opened: Opened) => void }) {
  const t = useT();
  const power = usePowerDialog();
  const fix = check.fix;
  const accept = (
    <Button size="sm" variant="ghost" onClick={() => open({ kind: "accept", check })}>
      {t("server.checks.acceptRisk")}
    </Button>
  );
  if (fix === null || fix === undefined || fix.kind === "none") return accept;
  if (fix.kind === "automatic") {
    return (
      <>
        {accept}
        <Button size="sm" onClick={() => open({ kind: "fix", check })}>
          {t("server.checks.fix")}
        </Button>
      </>
    );
  }
  if (fix.kind === "action") {
    const target = actionTarget(fix.endpoint);
    return (
      <>
        {accept}
        {target === "reboot" ? (
          <Button size="sm" onClick={() => power.open("reboot")}>
            {t("server.checks.scheduleReboot")}
          </Button>
        ) : target === "/server/services" ? (
          <Link to={target} search={{ all: true, state: "failed" }} className={buttonClassName("secondary", "sm")}>
            {t("server.checks.open")}
          </Link>
        ) : target !== null ? (
          <Link to={target} className={buttonClassName("secondary", "sm")}>
            {t("server.checks.open")}
          </Link>
        ) : (
          <Button size="sm" onClick={() => open({ kind: "guided", check })}>
            {t("server.checks.howToFix")}
          </Button>
        )}
      </>
    );
  }
  return (
    <>
      {accept}
      <Button size="sm" onClick={() => open({ kind: "guided", check })}>
        {t("server.checks.howToFix")}
      </Button>
    </>
  );
}

function FindingRow({ check, open }: { check: SecurityCheck; open: (opened: Opened) => void }) {
  const t = useT();
  const view = checkState(t, check);
  return (
    <li className="flex min-w-0 flex-col gap-1.5 px-5 py-3 sm:flex-row sm:items-start sm:gap-4">
      <div className="w-24 shrink-0 pt-0.5">
        <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-13 font-medium text-fg">{checkTitle(t, check)}</span>
        {/* What the check found, in Noust's words. */}
        {check.reason !== "" ? <span className="text-13 text-pretty text-fg-muted">{check.reason}</span> : null}
        <Evidence check={check} />
      </div>
      {check.status !== "unknown" ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <FindingActions check={check} open={open} />
        </div>
      ) : null}
    </li>
  );
}

function acceptedLine(t: T, check: SecurityCheck): string {
  const accepted = check.accepted;
  if (accepted === null || accepted === undefined) return "";
  const until = parseTimestamp(accepted.expires_at);
  return t("server.checks.acceptedBy", { actor: accepted.accepted_by, date: until ? formatDate(until, {}, t.locale) : accepted.expires_at });
}

function AcceptedRow({ check }: { check: SecurityCheck }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const withdraw = async (): Promise<void> => {
    try {
      await request("delete", "/api/server/security/risks/{check_id}", { params: { check_id: check.id } });
    } catch (error: unknown) {
      throw explainServerError(t, error, node);
    }
    void queryClient.invalidateQueries({ queryKey: serverKeys.security });
  };
  return (
    <li className="flex min-w-0 flex-col gap-1.5 px-5 py-3 sm:flex-row sm:items-start sm:gap-4">
      <div className="w-24 shrink-0 pt-0.5">
        <StatusPill state="stopped" label={t("server.checks.status.accepted")} appearance="inline" size="sm" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-13 font-medium text-fg">{checkTitle(t, check)}</span>
        <span className="text-13 text-fg-muted">{acceptedLine(t, check)}</span>
        {/* The reason as the person who accepted it wrote it. */}
        {check.accepted?.reason ? <span className="text-13 text-pretty text-fg">{check.accepted.reason}</span> : null}
      </div>
      <Button size="sm" variant="ghost" className="shrink-0" onClick={() => setConfirm(true)}>
        {t("server.checks.withdraw")}
      </Button>
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        friction="simple"
        server={node}
        destructive={false}
        title={t("server.checks.withdrawTitle", { check: checkTitle(t, check) })}
        description={t("server.checks.withdrawDescription")}
        actionLabel={t("server.checks.withdrawAction")}
        onConfirm={withdraw}
      />
    </li>
  );
}

function PassedList({ checks }: { checks: readonly SecurityCheck[] }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  if (checks.length === 0) return null;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div>
        <Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setOpen((current) => !current)}>
          {open ? t("server.checks.hidePassed") : t("server.checks.showPassed", { count: checks.length })}
        </Button>
      </div>
      {open ? (
        <Card padding="none">
          <ul aria-label={t("server.checks.passedLabel")} className="grid min-w-0 divide-border md:grid-cols-2">
            {checks.map((check) => {
              const view = checkState(t, check);
              return (
                <li key={check.id} className="flex min-w-0 items-center gap-3 border-b border-border px-5 py-2">
                  <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" className="w-28 shrink-0" />
                  <span className="min-w-0 truncate text-13 text-fg" title={check.reason}>
                    {checkTitle(t, check)}
                  </span>
                </li>
              );
            })}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}

/** The checks view of the Security tab. */
export function ChecksView() {
  const t = useT();
  const checks = useQuery(checksQuery());
  const [opened, setOpened] = useState<Opened>(null);
  if (checks.isError && checks.data === undefined) {
    return <ServerErrorBlock error={checks.error} title={t("server.checks.loadFailed")} onRetry={() => void checks.refetch()} retrying={checks.isRefetching} />;
  }
  if (checks.data === undefined) {
    return (
      <LoadingRegion label={t("server.checks.loading")} className="flex flex-col gap-3">
        <Skeleton className="h-64 w-full rounded-card" />
      </LoadingRegion>
    );
  }
  const sorted = sortChecks(checks.data.checks);
  const open = sorted.filter(isOpen);
  const accepted = sorted.filter((check) => check.status === "accepted");
  const rest = sorted.filter((check) => check.status === "pass" || check.status === "n/a");
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {open.length === 0 ? (
        <EmptyState variant="inline" title={t("server.checks.noFindings")} />
      ) : (
        <Card level={2} title={t("server.checks.findingsTitle", { count: open.length })} padding="none">
          <ul aria-label={t("server.checks.findingsLabel")} className="flex flex-col divide-y divide-border">
            {open.map((check) => (
              <FindingRow key={check.id} check={check} open={setOpened} />
            ))}
          </ul>
        </Card>
      )}
      {accepted.length > 0 ? (
        <Card level={2} title={t("server.checks.acceptedTitle", { count: accepted.length })} padding="none">
          <ul aria-label={t("server.checks.acceptedLabel")} className="flex flex-col divide-y divide-border">
            {accepted.map((check) => (
              <AcceptedRow key={check.id} check={check} />
            ))}
          </ul>
        </Card>
      ) : null}
      <PassedList checks={rest} />
      {opened?.kind === "fix" ? <FixDialog check={opened.check} onClose={() => setOpened(null)} /> : null}
      {opened?.kind === "guided" ? <GuidedDrawer check={opened.check} onClose={() => setOpened(null)} /> : null}
      {opened?.kind === "accept" ? <AcceptRiskDialog check={opened.check} onClose={() => setOpened(null)} /> : null}
    </div>
  );
}
