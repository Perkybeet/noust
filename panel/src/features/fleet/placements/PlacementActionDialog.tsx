import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode } from "react";

import { ElevationCancelledError } from "../../../api/client";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Subsection } from "../../../components/page/Subsection";
import { WizardActions } from "../../../components/page/Wizard";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { PlanView } from "../BulkActionDialog";
import { fleetKeys } from "../data";
import { COMMIT, DOMAIN, GIT_TAG, IMAGE_TAG, lacking, parseFailures, parseSerial, placementKeys, rolloutBody, runCopy, runRollout, sources } from "../placements";
import type { ActionAnswer, CopyBody, Placement, PlacementServer, RolloutBody, RolloutWhat } from "../placements";

/** The plan of a rollout or a copy, with what the central says about it, and the failures of either call. */
function Planned({
  action,
  planning,
  running,
  failedTitle,
  runFailedTitle,
  nothingToRun,
  onRetry,
  heading,
}: {
  action: "placement_rollout" | "placement_copy";
  planning: { isPending: boolean; isError: boolean; error: unknown; data: ActionAnswer | undefined };
  running: { isError: boolean; error: unknown };
  failedTitle: string;
  runFailedTitle: string;
  nothingToRun: string;
  onRetry: () => void;
  heading: string;
}) {
  const t = useT();
  if (planning.isPending) {
    return (
      <div aria-busy="true" className="flex flex-col gap-3">
        <span className="sr-only">{t("fleet.bulk.plan.loading")}</span>
        <Skeleton className="h-4 w-80 max-w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (planning.isError) return <ErrorBlock live error={planning.error} title={failedTitle} onRetry={onRetry} />;
  if (planning.data === undefined) return null;
  const plan = planning.data.plan;
  const notes: ReactNode[] = (plan.notes ?? []).map((note) => <li key={note}>{note}</li>);
  return (
    <div className="flex flex-col gap-4">
      <PlanView t={t} plan={plan} action={action} />
      {notes.length > 0 ? (
        <Subsection title={heading}>
          <ul className="flex list-disc flex-col gap-1 pl-5 text-13 text-fg-muted">{notes}</ul>
        </Subsection>
      ) : null}
      {(plan.summary["run"] ?? 0) === 0 ? <Notice tone="warning">{nothingToRun}</Notice> : null}
      {running.isError && !(running.error instanceof ElevationCancelledError) ? <ErrorBlock live error={running.error} title={runFailedTitle} /> : null}
    </div>
  );
}

/** What starting a planned action does: close, refresh the fleet and open the job that follows. */
function useStart(onClose: () => void) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  return (answer: ActionAnswer): void => {
    void queryClient.invalidateQueries({ queryKey: fleetKeys.jobs });
    void queryClient.invalidateQueries({ queryKey: placementKeys.all });
    const job = answer.job;
    if (job === null || job === undefined) return;
    onClose();
    void navigate({ to: "/fleet/jobs/$id", params: { id: job.job_id } });
  };
}

// ---------------------------------------------------------------------------------------
// Rolling it out

export interface RolloutDialogProps {
  placement: Placement;
  /** The servers it runs on now, for the choice of the one that goes first. */
  members: readonly string[];
  onClose: () => void;
}

type RolloutField = "value" | "serial" | "failures";

/**
 * Rolling an application out over its servers. What to deploy and how, then the plan the central
 * makes before anything runs - which server goes first and alone, which are skipped and why - and
 * then the job, followed server by server. The commit the first server ends on is what the others
 * are asked for.
 */
export function RolloutDialog({ placement, members, onClose }: RolloutDialogProps) {
  const t = useT();
  const start = useStart(onClose);
  const [step, setStep] = useState<"form" | "plan">("form");
  const [what, setWhat] = useState<RolloutWhat>("head");
  const [value, setValue] = useState("");
  const [force, setForce] = useState(false);
  const [rollback, setRollback] = useState(placement.rollback_on_failure);
  const [serial, setSerial] = useState("");
  const [failures, setFailures] = useState("");
  const [canary, setCanary] = useState("");
  const [errors, setErrors] = useState<Partial<Record<RolloutField, string>>>({});

  const body = (plan: boolean): RolloutBody | null => rolloutBody({ what, value, force, rollback, serial, failures, canary, plan });
  const planning = useMutation({ mutationFn: (request: RolloutBody) => runRollout(placement.domain, request) });
  const running = useMutation({ mutationFn: (request: RolloutBody) => runRollout(placement.domain, request), onSuccess: start });

  const goToPlan = (): void => {
    const found: Partial<Record<RolloutField, string>> = {};
    const text = value.trim();
    if (what === "commit" && !COMMIT.test(text)) found.value = t("placements.rollout.commitInvalid");
    if (what === "tag" && (!GIT_TAG.test(text) || text.includes(".."))) found.value = t("placements.rollout.tagInvalid");
    if (what === "image" && !IMAGE_TAG.test(text)) found.value = t("placements.rollout.imageInvalid");
    if (parseSerial(serial) === undefined) found.serial = t("placements.form.serialInvalid");
    if (parseFailures(failures) === undefined) found.failures = t("placements.form.failuresInvalid");
    setErrors(found);
    const request = body(true);
    if (Object.keys(found).length > 0 || request === null) return;
    setStep("plan");
    planning.mutate(request);
  };

  const runCount = planning.data?.plan.summary["run"] ?? 0;
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !running.isPending) onClose();
      }}
      size="lg"
      title={t("placements.rollout.title", { domain: placement.domain })}
      description={t("placements.rollout.description")}
      footer={
        <WizardActions
          className="w-full"
          {...(step === "plan"
            ? {
                back: {
                  label: t("placements.rollout.back"),
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
              ? { label: t("placements.rollout.showPlan"), onClick: goToPlan }
              : {
                  label: t("placements.rollout.run", { count: runCount }),
                  loading: running.isPending,
                  onClick: () => {
                    const request = body(false);
                    if (request !== null) running.mutate(request);
                  },
                }
          }
          {...(step === "plan" && planning.data !== undefined && runCount === 0 ? { missing: t("placements.rollout.nothingToRun") } : {})}
        />
      }
    >
      {step === "form" ? (
        <div className="flex flex-col gap-5">
          <ChoiceCards
            legend={t("placements.rollout.whatLegend")}
            value={what}
            onValueChange={(next) => {
              setWhat(next);
              setErrors({});
            }}
            options={[
              { value: "head", label: t("placements.rollout.head"), description: t("placements.rollout.headHelp") },
              { value: "commit", label: t("placements.rollout.commit"), description: t("placements.rollout.commitHelp") },
              { value: "tag", label: t("placements.rollout.tag"), description: t("placements.rollout.tagHelp") },
              { value: "image", label: t("placements.rollout.image"), description: t("placements.rollout.imageHelp") },
            ]}
          />
          {what !== "head" ? (
            <Field
              label={t(what === "commit" ? "placements.rollout.commitLabel" : what === "tag" ? "placements.rollout.tagLabel" : "placements.rollout.imageLabel")}
              error={errors.value}
              className="max-w-sm"
            >
              <Input
                mono
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                value={value}
                onValueChange={(next: string) => {
                  setValue(next);
                }}
              />
            </Field>
          ) : null}
          <Checkbox
            label={t("placements.rollout.force")}
            checked={force}
            onCheckedChange={setForce}
          />
          <Checkbox label={t("placements.rollout.rollback")} checked={rollback} onCheckedChange={setRollback} />
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
                    {
                      value: "@none",
                      label: t("placements.rollout.placementDefault", { value: placement.canary ?? members[0] ?? "-" }),
                    },
                    ...members.map((name) => ({ value: name, label: name })),
                  ]}
                />
              </Field>
            </div>
          </fieldset>
        </div>
      ) : (
        <Planned
          action="placement_rollout"
          planning={planning}
          running={running}
          failedTitle={t("placements.rollout.planFailed", { domain: placement.domain })}
          runFailedTitle={t("placements.rollout.runFailed", { domain: placement.domain })}
          nothingToRun={t("placements.rollout.nothingToRun")}
          heading={t("placements.rollout.planNotes")}
          onRetry={() => {
            const request = body(true);
            if (request !== null) planning.mutate(request);
          }}
        />
      )}
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------
// Copying it

export interface CopyDialogProps {
  placement: Placement;
  servers: readonly PlacementServer[];
  onClose: () => void;
}

/**
 * Putting an application on the servers that lack it. The source's own export is imported on each
 * destination and brought to the commit the source serves; sudo mode is asked when it starts, since an
 * import deploys as root.
 */
export function CopyDialog({ placement, servers, onClose }: CopyDialogProps) {
  const t = useT();
  const start = useStart(onClose);
  const from = sources(servers);
  const lack = lacking(servers);
  const [step, setStep] = useState<"form" | "plan">("form");
  const [source, setSource] = useState(from[0] ?? "");
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set(lack));
  const [newDomain, setNewDomain] = useState("");
  const [secrets, setSecrets] = useState(true);
  const [matchCommit, setMatchCommit] = useState(true);
  const [domainError, setDomainError] = useState<string | undefined>();

  const destinations = lack.filter((name) => chosen.has(name));
  const body = (plan: boolean): CopyBody => ({
    from_node: source,
    to: destinations,
    new_domain: newDomain.trim() === "" ? null : newDomain.trim().toLowerCase(),
    secrets,
    match_commit: matchCommit,
    plan,
  });
  const planning = useMutation({ mutationFn: (request: CopyBody) => runCopy(placement.domain, request) });
  const running = useMutation({ mutationFn: (request: CopyBody) => runCopy(placement.domain, request), onSuccess: start });

  const goToPlan = (): void => {
    if (newDomain.trim() !== "" && !DOMAIN.test(newDomain.trim().toLowerCase())) {
      setDomainError(t("placements.copy.newDomainInvalid"));
      return;
    }
    setDomainError(undefined);
    setStep("plan");
    planning.mutate(body(true));
  };

  let missing: string | undefined;
  if (step === "form") {
    if (from.length === 0) missing = t("placements.copy.noSource", { domain: placement.domain });
    else if (destinations.length === 0) missing = t("placements.copy.noDestinations", { domain: placement.domain });
  }
  const runCount = planning.data?.plan.summary["run"] ?? 0;
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !running.isPending) onClose();
      }}
      size="lg"
      title={t("placements.copy.title", { domain: placement.domain })}
      description={t("placements.copy.description")}
      footer={
        <WizardActions
          className="w-full"
          {...(step === "plan"
            ? {
                back: {
                  label: t("placements.copy.back"),
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
              ? { label: t("placements.copy.showPlan"), onClick: goToPlan }
              : {
                  label: t("placements.copy.run", { count: runCount }),
                  loading: running.isPending,
                  onClick: () => {
                    running.mutate(body(false));
                  },
                }
          }
          {...(missing !== undefined ? { missing } : {})}
        />
      }
    >
      {step === "form" ? (
        <div className="flex flex-col gap-5">
          <Field label={t("placements.copy.sourceLabel")} nativeLabel={false} description={t("placements.copy.sourceDescription")}>
            <Select
              value={source === "" ? null : source}
              onValueChange={setSource}
              options={from.map((name) => ({ value: name, label: name }))}
              mono
            />
          </Field>
          <fieldset className="flex flex-col gap-2">
            <legend className="text-13 font-medium text-fg">{t("placements.copy.destinationsLegend")}</legend>
            {lack.length === 0 ? <p className="text-13 text-fg-muted">{t("placements.copy.noDestinations", { domain: placement.domain })}</p> : null}
            <ul className="flex flex-col gap-1.5">
              {lack.map((name) => (
                <li key={name}>
                  <Checkbox
                    label={<Mono className="font-medium">{name}</Mono>}
                    checked={chosen.has(name)}
                    onCheckedChange={(checked) => {
                      setChosen((current) => {
                        const next = new Set(current);
                        if (checked) next.add(name);
                        else next.delete(name);
                        return next;
                      });
                    }}
                  />
                </li>
              ))}
            </ul>
          </fieldset>
          <Field label={t("placements.copy.newDomainLabel")} description={t("placements.copy.newDomainDescription")} error={domainError} optional className="max-w-sm">
            <Input
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              value={newDomain}
              onValueChange={(next: string) => {
                setNewDomain(next);
              }}
            />
          </Field>
          <Checkbox label={t("placements.copy.secrets")} description={t("placements.copy.secretsHelp")} checked={secrets} onCheckedChange={setSecrets} />
          <Checkbox
            label={t("placements.copy.matchCommit")}
            description={t("placements.copy.matchCommitHelp")}
            checked={matchCommit}
            onCheckedChange={setMatchCommit}
          />
        </div>
      ) : (
        <Planned
          action="placement_copy"
          planning={planning}
          running={running}
          failedTitle={t("placements.copy.planFailed", { domain: placement.domain })}
          runFailedTitle={t("placements.copy.runFailed", { domain: placement.domain })}
          nothingToRun={t("placements.rollout.nothingToRun")}
          heading={t("placements.rollout.planNotes")}
          onRetry={() => {
            planning.mutate(body(true));
          }}
        />
      )}
    </Dialog>
  );
}
