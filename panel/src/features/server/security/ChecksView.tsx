/**
 * The hardening checks of the machine, as one page that says what is not right and where it is put right. What
 * needs a look comes first, each as a line (its state, its name, "?" for why it was found and what the system
 * said, and the one action that deals with it, which is the way to the page where that is done); what is well comes
 * after, each with the place it is looked after; a risk somebody accepted says who and until when.
 */

import { Check, RotateCcw, RotateCw, ShieldQuestion, Wrench } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { request } from "../../../api/client";
import { Can } from "../../../components/page/permissions";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Disclosure } from "../../../components/ui/Disclosure";
import { HelpPopover } from "../../../components/ui/HelpPopover";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem } from "../../../components/ui/Menu";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusGlyph, StatusPill, stateTextClass } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { TextLink } from "../../../components/ui/TextLink";
import { RowMenu } from "../../../components/page/RowMenu";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatDate, parseTimestamp } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { namedActor } from "../../activity/data";
import { explainServerError, ServerQueryState } from "../errors";
import { usePowerDialog } from "../PowerDialog";
import { checksQuery, serverKeys } from "../queries";
import type { Checks, SecurityCheck } from "../queries";
import { AcceptRiskDialog, FixDialog, GuidedDrawer } from "./FixDialogs";
import { actionTarget, checkPlace, checkState, checkTitle, isOpen, sortChecks } from "./data";

type Opened = { kind: "fix" | "guided" | "accept"; check: SecurityCheck } | null;

/** Why a finding was made and what the system said, behind its "?": the reason in Noust's words, the evidence in the system's. */
function Why({ check }: { check: SecurityCheck }) {
  const t = useT();
  const title = checkTitle(t, check);
  if (check.reason === "" && check.evidence.length === 0) return null;
  return (
    <HelpPopover label={t("server.checks.whyLabel", { check: title })} title={title}>
      <div className="flex flex-col gap-2">
        {check.reason !== "" ? <p className="text-13 text-pretty text-fg">{check.reason}</p> : null}
        {check.evidence.length > 0 ? (
          <SystemOutput label={t("server.checks.evidenceLabel", { check: title })} maxHeight="max-h-40" className="rounded-control bg-bg-sunken p-2.5">
            {check.evidence.join("\n")}
          </SystemOutput>
        ) : null}
      </div>
    </HelpPopover>
  );
}

/** The one action of a finding: where it is put right, or the fix that puts it right here. */
function FindingAction({ check, open }: { check: SecurityCheck; open: (opened: Opened) => void }) {
  const t = useT();
  const power = usePowerDialog();
  const fix = check.fix;
  if (fix === null || fix === undefined || fix.kind === "none" || check.status === "unknown") return null;
  if (fix.kind === "automatic") {
    return (
      <Can permission="server.host_access">
        <Button icon={<Wrench aria-hidden="true" />} size="sm" onClick={() => open({ kind: "fix", check })}>
          {t("server.checks.fix")}
        </Button>
      </Can>
    );
  }
  if (fix.kind === "action") {
    const target = actionTarget(fix.endpoint);
    if (target === "reboot") {
      return (
        <Can permission="server.manage">
          <Button icon={<RotateCw aria-hidden="true" />} size="sm" onClick={() => power.open("reboot")}>
            {t("server.checks.scheduleReboot")}
          </Button>
        </Can>
      );
    }
    if (target === "/server/services") {
      return (
        <Link to={target} search={{ all: true, state: "failed" }} className={buttonClassName("secondary", "sm")}>
          {t("server.checks.open")}
        </Link>
      );
    }
    if (target !== null) {
      return (
        <Link to={target} className={buttonClassName("secondary", "sm")}>
          {t("server.checks.open")}
        </Link>
      );
    }
  }
  return (
    <Button size="sm" onClick={() => open({ kind: "guided", check })}>
      {t("server.checks.howToFix")}
    </Button>
  );
}

function FindingRow({ check, open }: { check: SecurityCheck; open: (opened: Opened) => void }) {
  const t = useT();
  const view = checkState(t, check);
  const title = checkTitle(t, check);
  return (
    <li className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 px-5 py-2.5">
      <StatusGlyph state={view.state} size={14} className={`shrink-0 ${stateTextClass(view.state)}`} />
      <span className="sr-only">{view.label}</span>
      <span className="flex min-w-0 flex-1 basis-60 items-center gap-1.5">
        <span className="min-w-0 text-13 text-fg">{title}</span>
        <Why check={check} />
      </span>
      <span className="flex shrink-0 items-center gap-2">
        <FindingAction check={check} open={open} />
        {check.status !== "unknown" ? (
          <Can permission="security.manage">
            <RowMenu name={title}>
              <MenuItem icon={<ShieldQuestion />} onClick={() => open({ kind: "accept", check })}>
                {t("server.checks.acceptRisk")}
              </MenuItem>
            </RowMenu>
          </Can>
        ) : null}
      </span>
    </li>
  );
}

function acceptedLine(t: T, check: SecurityCheck): string {
  const accepted = check.accepted;
  if (accepted === null || accepted === undefined) return "";
  const until = parseTimestamp(accepted.expires_at);
  return t("server.checks.acceptedBy", { actor: namedActor(t, accepted.accepted_by), date: until ? formatDate(until, {}, t.locale) : accepted.expires_at });
}

function AcceptedRow({ check }: { check: SecurityCheck }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const withdraw = async (): Promise<void> => {
    try {
      // design-exception: write-without-permission the withdraw button is inside `Can` for security.manage
      await request("delete", "/api/server/security/risks/{check_id}", { params: { check_id: check.id } });
    } catch (error: unknown) {
      throw explainServerError(t, error, node);
    }
    void queryClient.invalidateQueries({ queryKey: serverKeys.security });
  };
  return (
    <li className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 px-5 py-2.5">
      <StatusPill state="stopped" label={t("server.checks.status.accepted")} appearance="inline" size="sm" className="w-24 shrink-0" />
      <div className="flex min-w-0 flex-1 basis-60 flex-col gap-0.5">
        <span className="text-13 text-fg">{checkTitle(t, check)}</span>
        <span className="text-12 text-fg-muted">{acceptedLine(t, check)}</span>
        {/* The reason as the person who accepted it wrote it. */}
        {check.accepted?.reason ? <span className="text-13 text-pretty text-fg">{check.accepted.reason}</span> : null}
      </div>
      <Can permission="security.manage">
        <Button icon={<RotateCcw aria-hidden="true" />} size="sm" variant="ghost" className="shrink-0" onClick={() => setConfirm(true)}>
          {t("server.checks.withdraw")}
        </Button>
      </Can>
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

/** A check that passed, with the page that looks after what it checked when there is one. */
function PassedRow({ check }: { check: SecurityCheck }) {
  const t = useT();
  const place = checkPlace(check);
  return (
    <li className="flex min-w-0 items-center gap-2.5 text-13 text-fg">
      <Check aria-hidden="true" className="size-icon-sm shrink-0 text-ok" />
      <span className="sr-only">{t("server.checks.status.passed")}</span>
      <span className="min-w-0">
        {checkTitle(t, check)}
        {place !== null ? (
          <>
            {" "}
            <TextLink to={place.to} size="ui">
              {t(place.label)}
            </TextLink>
          </>
        ) : null}
      </span>
    </li>
  );
}

/** The checks page. */
export function ChecksView() {
  const t = useT();
  const checks = useQuery(checksQuery());
  return (
    <ServerQueryState query={checks} label={t("server.checks.label")} skeleton={<SkeletonCard lines={6} />}>
      {(data) => <Findings data={data} />}
    </ServerQueryState>
  );
}

function Findings({ data }: { data: Checks }) {
  const t = useT();
  const [opened, setOpened] = useState<Opened>(null);
  const sorted = sortChecks(data.checks);
  const open = sorted.filter(isOpen);
  const accepted = sorted.filter((check) => check.status === "accepted");
  const passed = sorted.filter((check) => check.status === "pass");
  const notApplicable = sorted.filter((check) => check.status === "n/a");
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <SurfaceSection
        title={t("server.checks.lookTitle")}
        icon={OBJECT_ICONS.security}
        description={open.length === 0 ? t("server.checks.noFindings") : t("server.checks.lookDescription")}
        {...(open.length > 0 ? { status: <StatusPill state="warning" label={t("server.checks.findingsTitle", { count: open.length })} appearance="inline" size="sm" /> } : {})}
        flush={open.length > 0}
      >
        {open.length > 0 ? (
          <ul aria-label={t("server.checks.findingsLabel")} className="flex flex-col divide-y divide-border">
            {open.map((check) => (
              <FindingRow key={check.id} check={check} open={setOpened} />
            ))}
          </ul>
        ) : null}
      </SurfaceSection>
      {accepted.length > 0 ? (
        <SurfaceSection title={t("server.checks.acceptedTitle", { count: accepted.length })} icon={ICONS.warning} flush>
          <ul aria-label={t("server.checks.acceptedLabel")} className="flex flex-col divide-y divide-border">
            {accepted.map((check) => (
              <AcceptedRow key={check.id} check={check} />
            ))}
          </ul>
        </SurfaceSection>
      ) : null}
      {passed.length > 0 ? (
        <SurfaceSection title={t("server.checks.wellTitle")} icon={ICONS.success} status={<StatusPill state="running" label={t("server.checks.wellCount", { count: passed.length })} appearance="inline" size="sm" />}>
          <ul aria-label={t("server.checks.passedLabel")} className="grid min-w-0 gap-x-8 gap-y-2.5 md:grid-cols-2">
            {passed.map((check) => (
              <PassedRow key={check.id} check={check} />
            ))}
          </ul>
        </SurfaceSection>
      ) : null}
      {notApplicable.length > 0 ? (
        <Disclosure label={t("server.checks.notApplicable", { count: notApplicable.length })}>
          <ul aria-label={t("server.checks.notApplicableLabel")} className="flex flex-col gap-1.5 text-13 text-fg-muted">
            {notApplicable.map((check) => (
              <li key={check.id}>{checkTitle(t, check)}</li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
      <p className="text-13 text-fg-muted">
        {t.rich("server.checks.framework", {
          link: (
            <TextLink key="compliance" to="/settings/compliance" size="ui">
              {t("server.checks.frameworkLink")}
            </TextLink>
          ),
        })}
      </p>
      {opened?.kind === "fix" ? <FixDialog check={opened.check} onClose={() => setOpened(null)} /> : null}
      {opened?.kind === "guided" ? <GuidedDrawer check={opened.check} onClose={() => setOpened(null)} /> : null}
      {opened?.kind === "accept" ? <AcceptRiskDialog check={opened.check} onClose={() => setOpened(null)} /> : null}
    </div>
  );
}
