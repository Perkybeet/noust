import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { changeCompliance, complianceKeys, planCompliance } from "../../../api/queries/compliance";
import type { ComplianceChange, ComplianceControl, CompliancePlan, ComplianceState } from "../../../api/queries/compliance";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { Switch } from "../../../components/ui/Switch";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";
import { ChangeDialog } from "./ChangeDialog";
import { LockedMark } from "./FrameworksSection";
import { GROUPS, asText, controlSummary, controlTitle, formatValue, inGroup, lockedBy } from "./model";

interface ControlDialogProps {
  control: ComplianceControl;
  onClose: () => void;
  /** The change was refused with warnings: the confirmation takes it from here. */
  onWarnings: (request: ComplianceChange, plan: CompliancePlan) => void;
}

/** Changing a number or a choice. The server judges the value and says why when it refuses. */
function ControlDialog({ control, onClose, onWarnings }: ControlDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [text, setText] = useState(asText(control.operator));
  const [choice, setChoice] = useState(asText(control.operator) || (control.choices[0] ?? ""));
  const title = controlTitle(t, control.id);

  const save = useMutation({
    mutationFn: async () => {
      const value: unknown = control.kind === "choice" ? choice : text.trim() === "" ? null : Number(text.trim());
      const request: ComplianceChange = { controls: { [control.id]: value } };
      const plan = await planCompliance(request);
      if (plan.warnings.length > 0) return { request, plan };
      await changeCompliance(request);
      return null;
    },
    onSuccess: (outcome) => {
      if (outcome !== null) {
        onWarnings(outcome.request, outcome.plan);
        return;
      }
      toast.success(t("complianceSetup.controls.changed", { name: title }));
      void queryClient.invalidateQueries({ queryKey: complianceKeys.all });
      onClose();
    },
  });

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      title={t("complianceSetup.controls.dialog.title", { name: title })}
      description={controlSummary(t, control.id)}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("complianceSetup.controls.dialog.save")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
          event.preventDefault();
          if (!save.isPending) save.mutate();
        }}
      >
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("complianceSetup.controls.dialog.failed")} /> : null}
        <Field
          label={t("complianceSetup.controls.dialog.value")}
          nativeLabel={control.kind !== "choice"}
          description={
            control.locked
              ? t("complianceSetup.controls.dialog.stricter", { floor: formatValue(t, control, control.floor) })
              : control.kind === "choice"
                ? undefined
                : t("complianceSetup.controls.dialog.noLimit")
          }
        >
          {control.kind === "choice" ? (
            <Select
              value={choice}
              onValueChange={setChoice}
              options={control.choices.map((word) => ({ value: word, label: formatValue(t, control, word) }))}
            />
          ) : (
            <Input
              type="number"
              inputMode="numeric"
              min={0}
              value={text}
              onChange={(event) => {
                setText(event.target.value);
              }}
              suffix={control.unit}
            />
          )}
        </Field>
      </form>
    </Dialog>
  );
}

interface ControlRowProps {
  control: ComplianceControl;
  state: ComplianceState;
  canManage: boolean;
  busy: boolean;
  onFlag: (control: ComplianceControl, next: boolean) => void;
  onEdit: (control: ComplianceControl) => void;
}

function ControlRow({ control, state, canManage, busy, onFlag, onEdit }: ControlRowProps) {
  const t = useT();
  const title = controlTitle(t, control.id);
  const locked = control.locked;
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3 first:pt-0 last:pb-0">
      <div className="min-w-0 flex-1">
        <p className="text-13 font-medium text-fg">{title}</p>
        <p className="max-w-measure text-12 text-pretty text-fg-muted">{controlSummary(t, control.id)}</p>
        {locked ? (
          <p className="mt-0.5 flex items-center gap-1 text-12 text-fg-muted">
            <LockedMark />
            {t("complianceSetup.controls.lockedBy", { names: lockedBy(state.frameworks, control) })}
          </p>
        ) : null}
        {control.check_only ? <p className="mt-0.5 text-12 text-fg-faint">{t("complianceSetup.controls.checkOnly")}</p> : null}
      </div>
      <div className="flex shrink-0 items-center gap-3">
        {control.kind === "flag" ? (
          <Switch
            aria-label={title}
            checked={control.value === true}
            disabled={!canManage || busy || (locked && control.value === true)}
            onCheckedChange={(next) => {
              onFlag(control, next);
            }}
          />
        ) : (
          <>
            <span className="text-13 text-fg">{formatValue(t, control, control.value)}</span>
            {canManage ? (
              <Button
                size="sm"
                variant="secondary"
                aria-label={`${t("complianceSetup.controls.change")} ${title}`}
                disabled={busy}
                onClick={() => {
                  onEdit(control);
                }}
              >
                {t("complianceSetup.controls.change")}
              </Button>
            ) : null}
          </>
        )}
      </div>
    </li>
  );
}

/**
 * Every control with the value in force, which enabled framework forces it (a forced one is locked
 * and says so), and its own setting. A switch that tightens applies at once unless the server
 * has something to say first; one that loosens asks "Confirm it's you" through the usual dialog.
 */
export function ControlsSection({ state, canManage }: { state: ComplianceState; canManage: boolean }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<ComplianceControl | null>(null);
  const [confirming, setConfirming] = useState<{ request: ComplianceChange; plan: CompliancePlan; title: string } | null>(null);
  const names = new Map(state.frameworks.map((framework) => [framework.id, framework.name]));

  const flip = useMutation({
    mutationFn: async ({ control, next }: { control: ComplianceControl; next: boolean }) => {
      const request: ComplianceChange = { controls: { [control.id]: next } };
      const plan = await planCompliance(request);
      if (plan.warnings.length > 0) return { request, plan, control };
      await changeCompliance(request);
      return { control, plan: null as CompliancePlan | null, request };
    },
    onSuccess: ({ control, plan, request }) => {
      if (plan !== null) {
        setConfirming({ request, plan, title: controlTitle(t, control.id) });
        return;
      }
      toast.success(t("complianceSetup.controls.changed", { name: controlTitle(t, control.id) }));
      void queryClient.invalidateQueries({ queryKey: complianceKeys.all });
    },
  });

  return (
    <Section level={3} title={t("complianceSetup.controls.title")} description={t("complianceSetup.controls.description")}>
      {flip.isError ? <ErrorBlock live compact error={flip.error} title={t("complianceSetup.controls.dialog.failed")} /> : null}
      {GROUPS.map((group) => {
        const rows = inGroup(state.controls, group);
        if (rows.length === 0) return null;
        return (
          <Subsection key={group} level={4} title={t(`complianceSetup.controls.group.${group}` as PlainKey)}>
            <Card padding="sm">
              <ul className="flex flex-col divide-y divide-border">
                {rows.map((control) => (
                  <ControlRow
                    key={control.id}
                    control={control}
                    state={state}
                    canManage={canManage}
                    busy={flip.isPending}
                    onFlag={(chosen, next) => {
                      flip.mutate({ control: chosen, next });
                    }}
                    onEdit={setEditing}
                  />
                ))}
              </ul>
            </Card>
          </Subsection>
        );
      })}
      {editing !== null ? (
        <ControlDialog
          control={editing}
          onClose={() => {
            setEditing(null);
          }}
          onWarnings={(request, plan) => {
            setConfirming({ request, plan, title: controlTitle(t, editing.id) });
            setEditing(null);
          }}
        />
      ) : null}
      {confirming !== null ? (
        <ChangeDialog
          request={confirming.request}
          plan={confirming.plan}
          title={t("complianceSetup.controls.dialog.title", { name: confirming.title })}
          lead={t("complianceSetup.dialog.enableLead")}
          action={t("complianceSetup.controls.dialog.save")}
          done={t("complianceSetup.controls.changed", { name: confirming.title })}
          controls={state.controls}
          names={names}
          onClose={() => {
            setConfirming(null);
          }}
        />
      ) : null}
    </Section>
  );
}
