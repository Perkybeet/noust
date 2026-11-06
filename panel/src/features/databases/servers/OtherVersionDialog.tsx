import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { instanceCatalogQuery, instanceKeys } from "../../../api/queries/databaseInstances";
import type { CatalogEngine } from "../../../api/queries/databaseInstances";
import { jobKeys } from "../../../api/queries/jobs";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field, FieldGroup } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { formatMonth } from "../../../lib/format";
import { MissingSoftware } from "../../server/software/MissingSoftware";
import { releaseOf, supportLine, supportOf } from "../servers";

export interface OtherVersionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The job that makes the server, to follow; and what it is called. */
  onQueued: (jobId: string, name: string) => void;
  /** The engine the dialog opens on, as a server's own menu offers it ("Another version of PostgreSQL"). */
  flavour?: string;
}

/** The engines whose official image runs a SQL script the first time it starts. */
const RUNS_SQL = new Set(["postgresql", "mysql", "mariadb"]);

/** A name a server of Noust's may have: lower-case words joined by single dashes, starting with a letter. */
const NAME = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;

type Errors = Partial<Record<"name" | "engine" | "version" | "memory" | "port" | "accept", string>>;

/** The name a server is given when the person gives none: its engine and release, as the CLI would say it. */
export function suggestedName(flavour: string, version: string): string {
  const release = releaseOf(flavour, version) ?? version;
  return `${flavour}-${release.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`.replace(/-+$/, "").slice(0, 24);
}

/**
 * "Another version": a database server of another version of an engine, or of one this machine lacks,
 * made in a container from the engine's official image pinned by digest, on 127.0.0.1 only and held to a
 * memory limit (`POST /api/databases/instances`, sudo mode). Each version says where it stands in its
 * project's support beside its number, and one that no longer gets security fixes is made only after the
 * person says they know it. Everything else (the name, the memory, the port) is chosen for them.
 */
export function OtherVersionDialog({ open, onOpenChange, onQueued, flavour: preset }: OtherVersionDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const catalog = useQuery({ ...instanceCatalogQuery(), enabled: open });
  const engines: readonly CatalogEngine[] = catalog.data?.engines ?? [];
  const now = useMemo(() => new Date(), []);
  const [flavour, setFlavour] = useState<string | null>(preset ?? null);
  const [version, setVersion] = useState<string | null>(null);
  const [variantName, setVariantName] = useState("");
  const [name, setName] = useState("");
  const [memory, setMemory] = useState("");
  const [port, setPort] = useState("");
  const [script, setScript] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [errors, setErrors] = useState<Errors>({});

  const chosen = engines.find((entry) => entry.flavour === flavour) ?? null;
  // An image of the engine with something added (PostgreSQL with pgvector) offers its own versions.
  const variants = chosen?.variants ?? [];
  const variant = variants.find((entry) => entry.name === variantName) ?? null;
  // Newest first: what most people want is the top of the list.
  const versions = [...(variant?.versions ?? chosen?.versions ?? [])].reverse();
  const picked = versions.find((entry) => entry.version === version) ?? versions.find((entry) => entry.default) ?? versions[0] ?? null;
  const support = picked === null ? null : supportOf({ support: picked.support, end_of_life: picked.end_of_life ?? null, runs_in: "noust_container" }, now);
  const needsAcceptance = picked?.unsupported === true;
  const suggestion = chosen !== null && picked !== null ? suggestedName(chosen.flavour, picked.version) : "";

  const reset = (): void => {
    setFlavour(preset ?? null);
    setVersion(null);
    setVariantName("");
    setName("");
    setMemory("");
    setPort("");
    setScript("");
    setAccepted(false);
    setErrors({});
  };

  const made = name.trim() === "" ? suggestion : name.trim();
  const make = useMutation({
    mutationFn: () => {
      if (chosen === null || picked === null) throw new Error("no engine");
      return request("post", "/api/databases/instances", {
        body: {
          name: made,
          engine: chosen.flavour,
          version: picked.version,
          ...(variant === null ? {} : { variant: variant.name }),
          ...(memory.trim() === "" ? {} : { memory_mb: Number(memory.trim()) }),
          accept_untested: false,
          unsupported_ok: needsAcceptance && accepted,
          ...(port.trim() === "" ? {} : { port: Number(port.trim()) }),
          ...(script.trim() === "" || !RUNS_SQL.has(chosen.flavour) ? {} : { init_scripts: [{ name: "init.sql", content: script }] }),
        },
      });
    },
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      void queryClient.invalidateQueries({ queryKey: instanceKeys.all });
      onQueued(accepted.job_id, made);
      onOpenChange(false);
      reset();
    },
    onError: (error) => {
      if (!isApiError(error) || error.fields === null) return;
      const found: Errors = {};
      for (const field of ["name", "engine", "version", "port"] as const) {
        const message = error.fields[field];
        if (message !== undefined) found[field] = message;
      }
      if (error.fields["memory_mb"] !== undefined) found.memory = error.fields["memory_mb"];
      if (error.fields["unsupported_ok"] !== undefined) found.accept = error.fields["unsupported_ok"];
      if (Object.keys(found).length > 0) setErrors(found);
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: Errors = {};
    if (chosen === null) found.engine = t("dbServers.other.engineRequired");
    else if (picked === null) found.version = t("dbServers.other.versionRequired");
    if (name.trim() !== "" && (!NAME.test(name.trim()) || name.trim().length > 24)) found.name = t("dbServers.other.nameInvalid");
    if (memory.trim() !== "") {
      const megabytes = Number(memory.trim());
      if (!Number.isInteger(megabytes) || megabytes < 1) found.memory = t("dbServers.other.memoryInvalid");
      else if (chosen !== null && megabytes < chosen.min_memory_mb) found.memory = t("dbServers.other.memoryLeast", { least: chosen.min_memory_mb });
    }
    if (port.trim() !== "" && !(Number.isInteger(Number(port.trim())) && Number(port.trim()) >= 1024 && Number(port.trim()) <= 65535)) found.port = t("dbServers.other.portInvalid");
    if (needsAcceptance && !accepted) found.accept = t("dbServers.other.unsupportedAcceptRequired");
    setErrors(found);
    if (Object.keys(found).length === 0) make.mutate();
  };

  const failure = make.isError && !(isApiError(make.error) && make.error.fields !== null && Object.keys(make.error.fields).length > 0) ? make.error : null;
  const formId = "other-version-form";
  const hiddenError = errors.name !== undefined || errors.memory !== undefined || errors.port !== undefined;
  const pickedTitle = chosen !== null && picked !== null ? { engine: chosen.display_name, version: picked.version } : null;
  const date = support?.date == null ? "" : formatMonth(new Date(`${support.date.slice(0, 10)}T12:00:00Z`), t.locale);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          reset();
          make.reset();
        }
      }}
      size="md"
      title={t("dbServers.other.title")}
      description={t("dbServers.other.description")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("common.words.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" loading={make.isPending}>
            {t("dbServers.other.submit")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        <MissingSoftware component="docker" />
        {catalog.isError ? <ErrorBlock compact error={catalog.error} title={t("dbServers.other.catalogFailed")} onRetry={() => void catalog.refetch()} /> : null}
        <Field label={t("dbServers.other.engine")} nativeLabel={false} error={errors.engine}>
          <Select
            value={chosen?.flavour ?? null}
            placeholder={t("dbServers.other.enginePlaceholder")}
            onValueChange={(value) => {
              setFlavour(value);
              setVersion(null);
              setVariantName("");
              setAccepted(false);
              setErrors((previous) => {
                const next = { ...previous };
                delete next.engine;
                return next;
              });
            }}
            options={engines.map((entry) => ({ value: entry.flavour, label: entry.display_name }))}
          />
        </Field>
        {chosen !== null ? (
          <Field label={t("dbServers.other.version")} nativeLabel={false} error={errors.version}>
            <Select
              value={picked?.version ?? null}
              onValueChange={(value) => {
                setVersion(value);
                setAccepted(false);
              }}
              options={versions.map((entry) => {
                const line = supportLine(t, supportOf({ support: entry.support, end_of_life: entry.end_of_life ?? null, runs_in: "noust_container" }, now));
                const base = entry.default ? t("dbServers.other.versionDefault", { version: entry.version }) : entry.version;
                return { value: entry.version, label: line === null ? base : t("dbServers.other.versionWith", { version: base, support: line }) };
              })}
            />
          </Field>
        ) : null}
        {needsAcceptance && pickedTitle !== null ? (
          <div className="flex flex-col gap-3">
            <Notice tone={support?.kind === "ended" ? "error" : "warning"} title={t("dbServers.other.unsupportedTitle")}>
              {support?.kind === "ended"
                ? t("dbServers.other.unsupportedEnded", { ...pickedTitle, date })
                : t("dbServers.other.unsupportedSoon", { ...pickedTitle, date })}
            </Notice>
            <FieldGroup legend={t("dbServers.other.unsupportedLegend")} error={errors.accept}>
              <Checkbox label={t("dbServers.other.unsupportedAccept")} checked={accepted} onCheckedChange={setAccepted} />
            </FieldGroup>
          </div>
        ) : null}
        {variant !== null && variant.platforms.length < 2 ? (
          <Notice tone="warning" title={t("dbServers.other.platformTitle")}>
            {t("dbServers.other.platformBody", { platforms: variant.platforms.join(", ") })}
          </Notice>
        ) : null}
        {/* An error in what is folded opens it (a new key remounts it open): a refusal nobody can see is not a refusal. */}
        <Disclosure key={hiddenError ? "open" : "folded"} label={t("dbServers.other.more")} defaultOpen={hiddenError}>
          <div className="flex flex-col gap-5 pt-2">
            <Field label={t("common.words.name")} optional error={errors.name} description={t("dbServers.other.nameHelp")}>
              <Input mono value={name} onValueChange={setName} placeholder={suggestion} autoComplete="off" autoCapitalize="off" spellCheck={false} />
            </Field>
            {variants.length > 0 ? (
              <Field label={t("dbServers.other.image")} nativeLabel={false} optional description={variant?.description ?? t("dbServers.other.imageHelp")}>
                <Select
                  value={variantName}
                  onValueChange={(value) => {
                    setVariantName(value);
                    setVersion(null);
                  }}
                  options={[
                    { value: "", label: t("dbServers.other.imageOwn", { engine: chosen?.display_name ?? "" }), hint: chosen?.image ?? "" },
                    ...variants.map((entry) => ({ value: entry.name, label: entry.display_name, hint: entry.image })),
                  ]}
                />
              </Field>
            ) : null}
            <Field
              label={t("dbServers.other.memory")}
              optional
              error={errors.memory}
              description={chosen !== null ? t("dbServers.other.memoryHelp", { least: chosen.min_memory_mb, suggested: chosen.default_memory_mb }) : undefined}
            >
              <Input mono inputMode="numeric" value={memory} onValueChange={setMemory} suffix="MB" placeholder={chosen !== null ? String(chosen.default_memory_mb) : ""} className="w-44" />
            </Field>
            <Field label={t("dbServers.other.port")} optional error={errors.port} description={t("dbServers.other.portHelp")}>
              <Input mono inputMode="numeric" value={port} onValueChange={setPort} className="w-44" />
            </Field>
            {chosen !== null && RUNS_SQL.has(chosen.flavour) ? (
              <Field label={t("dbServers.other.script")} optional description={t("dbServers.other.scriptHelp")}>
                <Textarea mono rows={5} value={script} onChange={(event) => setScript(event.target.value)} placeholder="CREATE TABLE ..." />
              </Field>
            ) : null}
          </div>
        </Disclosure>
        <p className="text-12 text-fg-muted">{t("dbServers.other.private")}</p>
        {failure !== null ? <ErrorBlock live compact error={failure} title={t("dbServers.other.failed")} /> : null}
      </form>
    </Dialog>
  );
}
