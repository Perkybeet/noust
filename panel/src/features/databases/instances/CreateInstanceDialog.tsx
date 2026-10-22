import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { instanceCatalogQuery, instanceKeys } from "../../../api/queries/databaseInstances";
import { jobKeys } from "../../../api/queries/jobs";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";

export interface CreateInstanceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The job that makes it, to follow; and what it is called. */
  onQueued: (jobId: string, name: string) => void;
}

/** The engines whose official image runs a SQL script the first time it starts. */
const RUNS_SQL = new Set(["postgresql", "mysql", "mariadb"]);

/** A name an instance may have: lower-case words joined by single dashes, starting with a letter. */
const NAME = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;

type Errors = Partial<Record<"name" | "engine" | "version" | "memory" | "port", string>>;

/**
 * "New instance": another version of an engine, or one the host lacks, run from the engine's
 * official image pinned by digest, on 127.0.0.1 only, held to a memory limit
 * (`POST /api/databases/instances`, sudo mode). The server checks it all before a job exists
 * (Docker, the name, the port, the memory it has left) and says which field is wrong.
 */
export function CreateInstanceDialog({ open, onOpenChange, onQueued }: CreateInstanceDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const catalog = useQuery({ ...instanceCatalogQuery(), enabled: open });
  const engines = catalog.data?.engines ?? [];
  const [flavour, setFlavour] = useState<string | null>(null);
  const [version, setVersion] = useState<string | null>(null);
  const [variantName, setVariantName] = useState("");
  const [name, setName] = useState("");
  const [memory, setMemory] = useState("");
  const [port, setPort] = useState("");
  const [script, setScript] = useState("");
  const [errors, setErrors] = useState<Errors>({});

  const chosen = engines.find((entry) => entry.flavour === flavour) ?? null;
  // An image of the engine with something added (PostgreSQL with pgvector) offers its own versions.
  const variants = chosen?.variants ?? [];
  const variant = variants.find((entry) => entry.name === variantName) ?? null;
  const versions = variant?.versions ?? chosen?.versions ?? [];
  const picked = versions.find((entry) => entry.version === version) ?? versions.find((entry) => entry.default) ?? versions.at(-1) ?? null;

  const reset = (): void => {
    setFlavour(null);
    setVersion(null);
    setVariantName("");
    setName("");
    setMemory("");
    setPort("");
    setScript("");
    setErrors({});
  };

  const make = useMutation({
    mutationFn: () => {
      if (chosen === null || picked === null) throw new Error("no engine");
      return request("post", "/api/databases/instances", {
        body: {
          name: name.trim(),
          engine: chosen.flavour,
          version: picked.version,
          ...(variant === null ? {} : { variant: variant.name }),
          memory_mb: Number(memory.trim()),
          accept_untested: false,
          ...(port.trim() === "" ? {} : { port: Number(port.trim()) }),
          ...(script.trim() === "" || !RUNS_SQL.has(chosen.flavour) ? {} : { init_scripts: [{ name: "init.sql", content: script }] }),
        },
      });
    },
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      void queryClient.invalidateQueries({ queryKey: instanceKeys.all });
      onQueued(accepted.job_id, name.trim());
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
      if (Object.keys(found).length > 0) setErrors(found);
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: Errors = {};
    if (chosen === null) found.engine = t("databases.newInstance.engineRequired");
    if (name.trim() === "") found.name = t("databases.newInstance.nameRequired");
    else if (!NAME.test(name.trim()) || name.trim().length > 24) found.name = t("databases.newInstance.nameInvalid");
    const megabytes = Number(memory.trim());
    if (memory.trim() === "" || !Number.isInteger(megabytes) || megabytes < 1) found.memory = t("databases.newInstance.memoryInvalid");
    else if (chosen !== null && megabytes < chosen.min_memory_mb) found.memory = t("databases.newInstance.memoryLeast", { least: chosen.min_memory_mb });
    if (port.trim() !== "" && !(Number.isInteger(Number(port.trim())) && Number(port.trim()) >= 1024 && Number(port.trim()) <= 65535)) found.port = t("databases.newInstance.portInvalid");
    setErrors(found);
    if (Object.keys(found).length === 0) make.mutate();
  };

  const failure = make.isError && !(isApiError(make.error) && make.error.fields !== null && Object.keys(make.error.fields).length > 0) ? make.error : null;
  const formId = "new-instance-form";
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
      title={t("databases.newInstance.title")}
      description={t("databases.newInstance.description")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("common.confirmDialog.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" loading={make.isPending}>
            {t("databases.newInstance.submit")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {catalog.isError ? <ErrorBlock compact error={catalog.error} title={t("databases.newInstance.catalogFailed")} onRetry={() => void catalog.refetch()} /> : null}
        <Field label={t("databases.newInstance.engine")} nativeLabel={false} error={errors.engine}>
          <Select
            value={chosen?.flavour ?? null}
            placeholder={t("databases.newInstance.enginePlaceholder")}
            onValueChange={(value) => {
              setFlavour(value);
              setVersion(null);
              setVariantName("");
              setErrors((previous) => {
                const next = { ...previous };
                delete next.engine;
                return next;
              });
            }}
            options={engines.map((entry) => ({ value: entry.flavour, label: entry.display_name }))}
          />
        </Field>
        {variants.length > 0 ? (
          <Field label={t("databases.newInstance.image")} nativeLabel={false} description={variant?.description ?? t("databases.newInstance.imageHelp")}>
            <Select
              value={variantName}
              onValueChange={(value) => {
                setVariantName(value);
                setVersion(null);
              }}
              options={[
                { value: "", label: t("databases.newInstance.imageOwn", { engine: chosen?.display_name ?? "" }), hint: chosen?.image ?? "" },
                ...variants.map((entry) => ({ value: entry.name, label: entry.display_name, hint: entry.image })),
              ]}
            />
          </Field>
        ) : null}
        {variant !== null && variant.platforms.length < 2 ? (
          <Notice tone="warning" title={t("databases.newInstance.platformTitle")}>
            {t("databases.newInstance.platformBody", { platforms: variant.platforms.join(", ") })}
          </Notice>
        ) : null}
        {chosen !== null ? (
          <Field label={t("databases.newInstance.version")} nativeLabel={false} error={errors.version} description={t("databases.newInstance.versionHelp")}>
            <Select
              value={picked?.version ?? null}
              onValueChange={(value) => setVersion(value)}
              options={versions.map((entry) => ({ value: entry.version, label: entry.default ? t("databases.newInstance.versionDefault", { version: entry.version }) : entry.version }))}
            />
          </Field>
        ) : null}
        <Field label={t("common.words.name")} error={errors.name} description={t("databases.newInstance.nameHelp")}>
          <Input mono value={name} onValueChange={setName} placeholder="pg17" autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field
          label={t("databases.newInstance.memory")}
          error={errors.memory}
          description={chosen !== null ? t("databases.newInstance.memoryHelp", { least: chosen.min_memory_mb, suggested: chosen.default_memory_mb }) : t("databases.newInstance.memoryHelpNone")}
        >
          <Input mono inputMode="numeric" value={memory} onValueChange={setMemory} suffix="MB" placeholder={chosen !== null ? String(chosen.default_memory_mb) : "1024"} className="w-44" />
        </Field>
        <Field label={t("databases.newInstance.port")} optional error={errors.port} description={t("databases.newInstance.portHelp")}>
          <Input mono inputMode="numeric" value={port} onValueChange={setPort} className="w-44" />
        </Field>
        {chosen !== null && RUNS_SQL.has(chosen.flavour) ? (
          <Field label={t("databases.newInstance.script")} optional description={t("databases.newInstance.scriptHelp")}>
            <Textarea mono rows={5} value={script} onChange={(event) => setScript(event.target.value)} placeholder="CREATE TABLE ..." />
          </Field>
        ) : null}
        <Notice title={t("databases.newInstance.privateTitle")}>{t("databases.newInstance.privateBody")}</Notice>
        {failure !== null ? <ErrorBlock live compact error={failure} title={t("databases.newInstance.failed")} /> : null}
        <CommandHint command="noust db instance create NAME --engine ENGINE --memory 1g" label={t("common.words.fromTerminal")} />
      </form>
    </Dialog>
  );
}
