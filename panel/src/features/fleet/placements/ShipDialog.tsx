import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { ElevationCancelledError } from "../../../api/client";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Subsection } from "../../../components/page/Subsection";
import { WizardActions } from "../../../components/page/Wizard";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { PlanView } from "../BulkActionDialog";
import { fleetKeys, fleetViewQuery, originOf } from "../data";
import { COMMIT, buildersQuery, clearBuilder, parseFailures, parseSerial, placementKeys, runShip, setBuilder, shipBody } from "../placements";
import type { ActionAnswer, Placement, ShipBody } from "../placements";
import { LoadingRegion } from "../../../components/page";

/** The server that builds an application, as the central recorded it; undefined when none. */
function builderOf(domain: string, builders: readonly { domain: string; node: string }[] | undefined): string | undefined {
  return builders?.find((row) => row.domain === domain)?.node;
}

type ShipField = "builder" | "targets" | "commit" | "serial" | "failures";

export interface ShipDialogProps {
  placement: Placement;
  onClose: () => void;
}

/**
 * Building an application on one server and running that build on the others. Which server builds,
 * which receive it, then the plan the central makes before anything runs - the first receiver goes
 * alone, the others are sent exactly what the builder made - and then the job, followed server by
 * server. Nothing is built on the servers that receive it.
 */
export function ShipDialog({ placement, onClose }: ShipDialogProps) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const servers = useQuery(fleetViewQuery("servers"));
  const recorded = useQuery(buildersQuery());
  const names = useMemo(
    () => (servers.data?.items ?? []).filter((row) => !originOf(row).local).map((row) => originOf(row).node),
    [servers.data],
  );
  const known = builderOf(placement.domain, recorded.data?.builders);

  const [step, setStep] = useState<"form" | "plan">("form");
  const [chosen, setChosen] = useState<string | null>(null);
  const builder = chosen ?? known ?? "";
  const [targets, setTargets] = useState<ReadonlySet<string> | null>(null);
  const [remember, setRemember] = useState(true);
  const [commit, setCommit] = useState("");
  const [force, setForce] = useState(false);
  const [schemaOk, setSchemaOk] = useState(false);
  const [rollback, setRollback] = useState(placement.rollback_on_failure);
  const [serial, setSerial] = useState("");
  const [failures, setFailures] = useState("");
  const [canary, setCanary] = useState("");
  const [errors, setErrors] = useState<Partial<Record<ShipField, string>>>({});

  // Until the operator touches them, every member except the builder receives it.
  const receiving = targets ?? new Set(placement.members.filter((name) => name !== builder));
  const receivers = placement.members.filter((name) => receiving.has(name) && name !== builder);

  const body = (plan: boolean): ShipBody | null =>
    shipBody({ builder, targets: receivers, commit, force, schemaChangedOk: schemaOk, rollback, serial, failures, canary, plan });
  const planning = useMutation({ mutationFn: (request: ShipBody) => runShip(placement.domain, request) });
  const running = useMutation({
    mutationFn: async (request: ShipBody): Promise<ActionAnswer> => {
      if (remember && builder !== known) await setBuilder(placement.domain, builder);
      return runShip(placement.domain, request);
    },
    onSuccess: (answer) => {
      void queryClient.invalidateQueries({ queryKey: fleetKeys.jobs });
      void queryClient.invalidateQueries({ queryKey: placementKeys.all });
      void queryClient.invalidateQueries({ queryKey: placementKeys.builders });
      const job = answer.job;
      if (job === null || job === undefined) return;
      onClose();
      void navigate({ to: "/fleet/jobs/$id", params: { id: job.job_id } });
    },
  });

  const goToPlan = (): void => {
    const found: Partial<Record<ShipField, string>> = {};
    if (builder === "") found.builder = t("placements.ship.noBuilder");
    if (receivers.length === 0) found.targets = t("placements.ship.noTargets");
    if (commit.trim() !== "" && !COMMIT.test(commit.trim())) found.commit = t("placements.ship.commitInvalid");
    if (parseSerial(serial) === undefined) found.serial = t("placements.form.serialInvalid");
    if (parseFailures(failures) === undefined) found.failures = t("placements.form.failuresInvalid");
    setErrors(found);
    const request = body(true);
    if (Object.keys(found).length > 0 || request === null) return;
    setStep("plan");
    planning.mutate(request);
  };

  const runCount = planning.data?.plan.summary["run"] ?? 0;
  const notes = planning.data?.plan.notes ?? [];
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !running.isPending) onClose();
      }}
      size="lg"
      title={t("placements.ship.title", { domain: placement.domain })}
      description={t("placements.ship.description")}
      footer={
        <WizardActions
          className="w-full"
          {...(step === "plan"
            ? {
                back: {
                  label: t("placements.ship.back"),
                  onClick: () => {
                    if (planning.isPending || running.isPending) return;
                    planning.reset();
                    running.reset();
                    setStep("form");
                  },
                },
              }
            : {})}
          next={
            step === "form"
              ? { label: t("placements.ship.showPlan"), onClick: goToPlan }
              : {
                  label: t("placements.ship.run", { count: runCount }),
                  loading: running.isPending,
                  onClick: () => {
                    const request = body(false);
                    if (request !== null) running.mutate(request);
                  },
                }
          }
          {...(step === "plan" && planning.data !== undefined && runCount === 0 ? { missing: t("placements.ship.nothingToRun") } : {})}
        />
      }
    >
      {step === "form" ? (
        <div className="flex flex-col gap-5">
          <Field label={t("placements.ship.builderLabel")} description={t("placements.ship.builderDescription")} error={errors.builder} nativeLabel={false}>
            <Select
              value={builder === "" ? null : builder}
              onValueChange={(next) => {
                setChosen(next);
                if (targets?.has(next)) {
                  const rest = new Set(targets);
                  rest.delete(next);
                  setTargets(rest);
                }
              }}
              options={names.map((name) => ({ value: name, label: name }))}
              mono
            />
          </Field>
          {builder !== "" && builder !== known ? (
            <Checkbox label={t("placements.ship.remember", { domain: placement.domain })} checked={remember} onCheckedChange={setRemember} />
          ) : null}
          <fieldset className="flex flex-col gap-2">
            <legend className="text-13 font-medium text-fg">{t("placements.ship.targetsLegend")}</legend>
            <ul className="flex flex-col gap-1.5">
              {placement.members.map((name) => (
                <li key={name}>
                  <Checkbox
                    label={<Mono className="font-medium">{name}</Mono>}
                    checked={receiving.has(name) && name !== builder}
                    disabled={name === builder}
                    onCheckedChange={(checked) => {
                      const next = new Set(receiving);
                      if (checked) next.add(name);
                      else next.delete(name);
                      setTargets(next);
                    }}
                  />
                </li>
              ))}
            </ul>
            {errors.targets !== undefined ? (
              <InlineMessage tone="error" live>{errors.targets}</InlineMessage>
            ) : null}
            {placement.members.includes(builder) ? <p className="text-13 text-fg-muted">{t("placements.ship.onlyBuilder")}</p> : null}
          </fieldset>
          <Field label={t("placements.ship.commitLabel")} description={t("placements.ship.commitDescription")} error={errors.commit} optional className="max-w-sm">
            <Input
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              value={commit}
              onValueChange={(next: string) => {
                setCommit(next);
              }}
            />
          </Field>
          <Checkbox label={t("placements.ship.force")} checked={force} onCheckedChange={setForce} />
          <Checkbox label={t("placements.ship.schema")} description={t("placements.ship.schemaHelp")} checked={schemaOk} onCheckedChange={setSchemaOk} />
          <Checkbox label={t("placements.ship.rollback")} checked={rollback} onCheckedChange={setRollback} />
          <fieldset className="flex flex-col gap-4">
            <legend className="mb-1 text-13 font-medium text-fg">{t("placements.rollout.howLegend")}</legend>
            <div className="grid gap-5 sm:grid-cols-3">
              <Field label={t("placements.rollout.serialLabel")} error={errors.serial} optional>
                <Input
                  mono
                  autoComplete="off"
                  placeholder={String(placement.serial ?? 1)}
                  value={serial}
                  onValueChange={(next: string) => {
                    setSerial(next);
                  }}
                />
              </Field>
              <Field label={t("placements.rollout.failuresLabel")} error={errors.failures} optional>
                <Input
                  mono
                  inputMode="numeric"
                  autoComplete="off"
                  placeholder={String(placement.max_failures ?? 0)}
                  value={failures}
                  onValueChange={(next: string) => {
                    setFailures(next);
                  }}
                />
              </Field>
              <Field label={t("placements.rollout.canaryLabel")} nativeLabel={false} optional>
                <Select
                  value={canary === "" ? "@none" : canary}
                  onValueChange={(next) => {
                    setCanary(next === "@none" ? "" : next);
                  }}
                  options={[
                    { value: "@none", label: t("placements.rollout.placementDefault", { value: placement.canary ?? receivers[0] ?? "-" }) },
                    ...receivers.map((name) => ({ value: name, label: name })),
                  ]}
                />
              </Field>
            </div>
          </fieldset>
        </div>
      ) : planning.isPending ? (
        <LoadingRegion label={t("fleet.bulk.plan.loading")} className="flex flex-col gap-3">
          <Skeleton className="h-4 w-80 max-w-full" />
          <Skeleton className="h-24 w-full" />
        </LoadingRegion>
      ) : planning.isError ? (
        <ErrorBlock
          live
          error={planning.error}
          title={t("placements.ship.planFailed", { domain: placement.domain })}
          onRetry={() => {
            const request = body(true);
            if (request !== null) planning.mutate(request);
          }}
        />
      ) : planning.data !== undefined ? (
        <div className="flex flex-col gap-4">
          <PlanView t={t} plan={planning.data.plan} action="placement_ship" />
          {notes.length > 0 ? (
            <Subsection title={t("placements.ship.planNotes")}>
              <ul className="flex list-disc flex-col gap-1 pl-5 text-13 text-fg-muted">
                {notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </Subsection>
          ) : null}
          {runCount === 0 ? <Notice tone="warning">{t("placements.ship.nothingToRun")}</Notice> : null}
          {running.isError && !(running.error instanceof ElevationCancelledError) ? (
            <ErrorBlock live error={running.error} title={t("placements.ship.runFailed", { domain: placement.domain })} />
          ) : null}
        </div>
      ) : null}
    </Dialog>
  );
}

export interface BuilderDialogProps {
  placement: Placement;
  onClose: () => void;
}

/**
 * Choosing which server builds an application for the others. It only records the choice on this
 * central: no server is asked, nothing is built until someone ships.
 */
export function BuilderDialog({ placement, onClose }: BuilderDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const servers = useQuery(fleetViewQuery("servers"));
  const recorded = useQuery(buildersQuery());
  const names = useMemo(
    () => (servers.data?.items ?? []).filter((row) => !originOf(row).local).map((row) => originOf(row).node),
    [servers.data],
  );
  const known = builderOf(placement.domain, recorded.data?.builders) ?? "";
  const [chosen, setChosen] = useState<string | null>(null);
  const value = chosen ?? known;

  const save = useMutation({
    mutationFn: async (node: string) => {
      if (node === "") await clearBuilder(placement.domain);
      else await setBuilder(placement.domain, node);
      return node;
    },
    onSuccess: (node) => {
      void queryClient.invalidateQueries({ queryKey: placementKeys.builders });
      toast.success(node === "" ? t("placements.builder.cleared", { domain: placement.domain }) : t("placements.builder.saved", { node, domain: placement.domain }));
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
      title={t("placements.builder.title", { domain: placement.domain })}
      description={t("placements.builder.description")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("placements.builder.cancel")}
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={value === known}
            onClick={() => {
              save.mutate(value);
            }}
          >
            {t("placements.builder.save")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t("placements.builder.label")} nativeLabel={false}>
          <Select
            value={value === "" ? "@none" : value}
            onValueChange={(next) => {
              setChosen(next === "@none" ? "" : next);
            }}
            options={[{ value: "@none", label: t("placements.builder.none") }, ...names.map((name) => ({ value: name, label: name }))]}
            mono
          />
        </Field>
        {save.isError ? <ErrorBlock live error={save.error} title={t("placements.builder.failed", { domain: placement.domain })} /> : null}
      </div>
    </Dialog>
  );
}
