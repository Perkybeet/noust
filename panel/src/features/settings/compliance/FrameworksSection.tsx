import { Lock } from "lucide-react";
import { useState } from "react";

import type { ComplianceFramework, ComplianceState } from "../../../api/queries/compliance";
import { Section } from "../../../components/page/Section";
import { Subsection } from "../../../components/page/Subsection";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { ChangeDialog } from "./ChangeDialog";
import { controlTitle, formatValue, frameworkSummary } from "./model";

interface FrameworkRowProps {
  framework: ComplianceFramework;
  state: ComplianceState;
  canManage: boolean;
  onChange: (framework: ComplianceFramework, enable: boolean) => void;
}

function stateLabel(t: T, framework: ComplianceFramework): string {
  if (!framework.enabled) return t("complianceSetup.frameworks.off");
  return framework.source === "profile" ? t("complianceSetup.frameworks.viaProfile") : t("complianceSetup.frameworks.on");
}

function WhatItTurnsOn({ framework, state }: { framework: ComplianceFramework; state: ComplianceState }) {
  const t = useT();
  const byId = new Map(state.controls.map((control) => [control.id, control]));
  // What enabling it would change, when it is off; the controls it is asked of, when it is on.
  const rows = framework.enabled
    ? state.controls.filter((control) => control.forced_by.includes(framework.id))
    : framework.changes.map((change) => byId.get(change.control)).filter((control) => control !== undefined);
  return (
    <div className="flex flex-col gap-4">
      {rows.length === 0 ? (
        <p className="text-13 text-fg-muted">{t("complianceSetup.frameworks.nothingNew")}</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {rows.map((control) => {
            const change = framework.changes.find((entry) => entry.control === control.id);
            return (
              <li key={control.id} className="flex flex-wrap items-baseline gap-x-2 text-13">
                <span className="text-fg">{controlTitle(t, control.id)}</span>
                <span className="text-fg-muted">
                  {change === undefined
                    ? formatValue(t, control, control.value)
                    : `${formatValue(t, control, change.before)} → ${formatValue(t, control, change.after)}`}
                </span>
                {change !== undefined ? <Mono tone="muted">{change.clauses.join(", ")}</Mono> : null}
              </li>
            );
          })}
        </ul>
      )}
      <Subsection level={4} title={t("complianceSetup.frameworks.organisational")}>
        <ul className="flex list-disc flex-col gap-1 pl-4 text-12 text-fg-muted">
          {framework.organisational.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </Subsection>
      {framework.not_covered.length > 0 ? (
        <Subsection level={4} title={t("complianceSetup.frameworks.notCovered")}>
          <ul className="flex list-disc flex-col gap-1 pl-4 text-12 text-fg-muted">
            {framework.not_covered.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Subsection>
      ) : null}
    </div>
  );
}

function FrameworkRow({ framework, state, canManage, onChange }: FrameworkRowProps) {
  const t = useT();
  const forced = state.controls.filter((control) => control.forced_by.includes(framework.id)).length;
  return (
    <Card
      padding="sm"
      as="section"
      title={framework.name}
      description={frameworkSummary(t, framework)}
      actions={
        canManage ? (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              onChange(framework, !framework.enabled);
            }}
          >
            {framework.enabled ? t("complianceSetup.frameworks.disable") : t("complianceSetup.frameworks.enable")}
          </Button>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Badge tone={framework.enabled ? "ok" : "neutral"}>{stateLabel(t, framework)}</Badge>
          <Badge>{t("complianceSetup.frameworks.controlsCount", { count: framework.requirement_count })}</Badge>
          <span className="text-12 text-fg-muted">
            {framework.enabled
              ? t("complianceSetup.frameworks.forcedHere", { count: forced })
              : framework.changes.length === 0
                ? t("complianceSetup.frameworks.nothingNew")
                : t("complianceSetup.frameworks.changesCount", { count: framework.changes.length })}
          </span>
          <span className="text-12 text-fg-faint">{framework.reference}</span>
        </div>
        <Disclosure label={t("complianceSetup.frameworks.show")}>
          <WhatItTurnsOn framework={framework} state={state} />
        </Disclosure>
      </div>
    </Card>
  );
}

/**
 * The frameworks an operator can switch on, each with one line on what it is and, before anything is
 * confirmed, the controls it will turn on and the clause each serves. Several can be on; the
 * strictest value of each control wins. Disabling one is elevated and audited by the server.
 */
export function FrameworksSection({ state, canManage }: { state: ComplianceState; canManage: boolean }) {
  const t = useT();
  const [asked, setAsked] = useState<{ framework: ComplianceFramework; enable: boolean } | null>(null);
  const names = new Map(state.frameworks.map((framework) => [framework.id, framework.name]));
  return (
    <Section level={3} title={t("complianceSetup.frameworks.title")} description={t("complianceSetup.frameworks.description")}>
      <div className="flex flex-col gap-3">
        {state.frameworks.map((framework) => (
          <FrameworkRow
            key={framework.id}
            framework={framework}
            state={state}
            canManage={canManage}
            onChange={(chosen, enable) => {
              setAsked({ framework: chosen, enable });
            }}
          />
        ))}
      </div>
      {asked !== null ? (
        <ChangeDialog
          request={asked.enable ? { enable: [asked.framework.id] } : { disable: [asked.framework.id] }}
          title={t(asked.enable ? "complianceSetup.dialog.enableTitle" : "complianceSetup.dialog.disableTitle", { name: asked.framework.name })}
          lead={asked.enable ? t("complianceSetup.dialog.enableLead") : t("complianceSetup.dialog.disableLead")}
          action={asked.enable ? t("complianceSetup.dialog.enableAction") : t("complianceSetup.dialog.disableAction")}
          loosens={!asked.enable}
          done={t(asked.enable ? "complianceSetup.dialog.turnedOn" : "complianceSetup.dialog.turnedOff", { name: asked.framework.name })}
          controls={state.controls}
          names={names}
          onClose={() => {
            setAsked(null);
          }}
        />
      ) : null}
    </Section>
  );
}

/** An icon for a control a framework locks. */
export function LockedMark() {
  return <Lock aria-hidden="true" className="size-3.5 shrink-0 text-fg-faint" />;
}
