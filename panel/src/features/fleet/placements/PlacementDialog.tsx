import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { fleetViewQuery, formatLabels, originOf, parseLabelSelector } from "../data";
import { DOMAIN, parseFailures, parseSerial, placementKeys, savePlacement } from "../placements";
import type { Placement, PlacementBody } from "../placements";

export interface PlacementDialogProps {
  /** The placement being changed; null records a new one. */
  placement: Placement | null;
  onClose: () => void;
}

/** What the form holds, as text, for a placement. */
export function formOf(placement: Placement | null) {
  return {
    domain: placement?.domain ?? "",
    nodes: new Set(placement?.nodes ?? []),
    labels: formatLabels(placement?.labels ?? {}),
    serial: placement?.serial === null || placement?.serial === undefined ? "" : String(placement.serial),
    failures: placement?.max_failures === null || placement?.max_failures === undefined ? "" : String(placement.max_failures),
    canary: placement?.canary ?? "",
    rollback: placement?.rollback_on_failure ?? true,
  };
}

/**
 * Where an application runs and how an update goes out over its servers. Recording it touches no
 * server: it is this central's own note, which the status, the rollout and the load balancer's list
 * all read.
 */
export function PlacementDialog({ placement, onClose }: PlacementDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const firstRef = useRef<HTMLInputElement>(null);
  const servers = useQuery(fleetViewQuery("servers"));
  const initial = useMemo(() => formOf(placement), [placement]);
  const [domain, setDomain] = useState(initial.domain);
  const [nodes, setNodes] = useState<ReadonlySet<string>>(initial.nodes);
  const [labels, setLabels] = useState(initial.labels);
  const [serial, setSerial] = useState(initial.serial);
  const [failures, setFailures] = useState(initial.failures);
  const [canary, setCanary] = useState(initial.canary);
  const [rollback, setRollback] = useState(initial.rollback);
  const [errors, setErrors] = useState<Partial<Record<"domain" | "nodes" | "labels" | "serial" | "failures", string>>>({});

  const names = useMemo(
    () => (servers.data?.items ?? []).filter((row) => !originOf(row).local).map((row) => originOf(row).node),
    [servers.data],
  );

  const save = useMutation({
    mutationFn: (body: PlacementBody) => savePlacement(domain.trim().toLowerCase(), body),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: placementKeys.all });
      toast.success(t("placements.form.saved", { domain: saved.domain }));
      onClose();
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (save.isPending) return;
    const found: typeof errors = {};
    const selector = parseLabelSelector(labels);
    const parsedSerial = parseSerial(serial);
    const parsedFailures = parseFailures(failures);
    if (!DOMAIN.test(domain.trim().toLowerCase())) found.domain = t("placements.form.domainInvalid");
    if (selector === null) found.labels = t("placements.form.labelInvalid");
    else if (nodes.size === 0 && Object.keys(selector).length === 0) found.nodes = t("placements.form.nothing");
    if (parsedSerial === undefined) found.serial = t("placements.form.serialInvalid");
    if (parsedFailures === undefined) found.failures = t("placements.form.failuresInvalid");
    setErrors(found);
    if (Object.keys(found).length > 0 || selector === null || parsedSerial === undefined || parsedFailures === undefined) {
      firstRef.current?.focus();
      return;
    }
    save.mutate({
      nodes: names.filter((name) => nodes.has(name)),
      labels: selector,
      serial: parsedSerial,
      max_failures: parsedFailures,
      canary: canary === "" || !nodes.has(canary) ? null : canary,
      rollback_on_failure: rollback,
    });
  };

  const chosen = names.filter((name) => nodes.has(name));
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="lg"
      initialFocus={firstRef}
      title={placement === null ? t("placements.form.titleNew") : t("placements.form.titleEdit", { domain: placement.domain })}
      description={t("placements.form.description")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("common.words.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        <Field label={t("placements.form.domainLabel")} description={t("placements.form.domainDescription")} error={errors.domain}>
          <Input
            ref={firstRef}
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="shop.example.com"
            readOnly={placement !== null}
            value={domain}
            onValueChange={(value: string) => {
              setDomain(value);
            }}
          />
        </Field>
        <fieldset className="flex flex-col gap-2">
          <legend className="text-13 font-medium text-fg">{t("placements.form.serversLegend")}</legend>
          <p className="text-13 text-fg-muted">{t("placements.form.serversDescription")}</p>
          {names.length === 0 && !servers.isPending ? <p className="text-13 text-fg-muted">{t("placements.form.noServers")}</p> : null}
          <ul className="flex flex-col gap-1.5">
            {names.map((name) => (
              <li key={name}>
                <Checkbox
                  label={<Mono className="font-medium">{name}</Mono>}
                  checked={nodes.has(name)}
                  onCheckedChange={(checked) => {
                    setNodes((current) => {
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
          {errors.nodes !== undefined ? (
            <p role="alert" className="text-13 text-fail">
              {errors.nodes}
            </p>
          ) : null}
        </fieldset>
        <Field label={t("placements.form.labelLabel")} description={t("placements.form.labelDescription")} error={errors.labels} optional>
          <Input
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="role=web"
            value={labels}
            onValueChange={(value: string) => {
              setLabels(value);
            }}
          />
        </Field>
        <div className="grid gap-5 sm:grid-cols-3">
          <Field label={t("placements.form.serialLabel")} description={t("placements.form.serialDescription")} error={errors.serial} optional>
            <Input
              mono
              autoComplete="off"
              placeholder="1"
              value={serial}
              onValueChange={(value: string) => {
                setSerial(value);
              }}
            />
          </Field>
          <Field label={t("placements.form.failuresLabel")} description={t("placements.form.failuresDescription")} error={errors.failures} optional>
            <Input
              mono
              inputMode="numeric"
              autoComplete="off"
              value={failures}
              onValueChange={(value: string) => {
                setFailures(value);
              }}
            />
          </Field>
          <Field label={t("placements.form.canaryLabel")} nativeLabel={false} description={t("placements.form.canaryDescription")} optional>
            <Select
              value={canary === "" || !nodes.has(canary) ? "@none" : canary}
              onValueChange={(value) => {
                setCanary(value === "@none" ? "" : value);
              }}
              options={[{ value: "@none", label: t("placements.form.noCanary") }, ...chosen.map((name) => ({ value: name, label: name }))]}
            />
          </Field>
        </div>
        <Checkbox
          label={t("placements.form.rollbackLabel")}
          description={t("placements.form.rollbackDescription")}
          checked={rollback}
          onCheckedChange={setRollback}
        />
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("placements.form.failed", { domain: domain.trim() || "-" })} /> : null}
      </form>
    </Dialog>
  );
}
