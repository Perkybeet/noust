import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { appsQuery } from "../../api/queries/apps";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { useBackupActions } from "./useBackupActions";

export interface CreateBackupDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Preselects the application; omit to let the operator choose. */
  domain?: string;
}

/**
 * Backs an application up now, with the full option set `CreateBackupRequest` takes: the
 * application's files always, its .env and databases by choice, and what most operators never
 * need (build output, node_modules, Docker volumes, tags) folded under "More options".
 */
export function CreateBackupDialog({ open, onOpenChange, domain: fixedDomain }: CreateBackupDialogProps) {
  const t = useT();
  const formId = useId();
  const apps = useQuery({ ...appsQuery(), enabled: open && fixedDomain === undefined });
  const [domain, setDomain] = useState(fixedDomain ?? "");
  const [description, setDescription] = useState("");
  const [includeEnv, setIncludeEnv] = useState(true);
  const [includeDatabase, setIncludeDatabase] = useState(false);
  const [includeNodeModules, setIncludeNodeModules] = useState(false);
  const [includeBuild, setIncludeBuild] = useState(false);
  const [includeDockerVolumes, setIncludeDockerVolumes] = useState(false);
  const [redisMethod, setRedisMethod] = useState("rdb");
  const [tags, setTags] = useState("");
  const { create } = useBackupActions();
  const target = fixedDomain ?? domain;

  const reset = (): void => {
    setDomain(fixedDomain ?? "");
    setDescription("");
    setIncludeEnv(true);
    setIncludeDatabase(false);
    setIncludeNodeModules(false);
    setIncludeBuild(false);
    setIncludeDockerVolumes(false);
    setRedisMethod("rdb");
    setTags("");
    create.reset();
  };

  const close = (next: boolean): void => {
    if (!next && create.isPending) return;
    onOpenChange(next);
    if (!next) reset();
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (target.trim() === "") return;
    create.mutate(
      {
        domain: target.trim(),
        description: description.trim(),
        includeEnv,
        includeNodeModules,
        includeBuild,
        includeDatabase,
        includeDockerVolumes,
        redisMethod,
        tags: tags
          .split(",")
          .map((tag) => tag.trim())
          .filter((tag) => tag !== ""),
      },
      { onSuccess: () => close(false) },
    );
  };

  const domainOptions = (apps.data?.apps ?? []).map((app) => ({ value: app.domain, label: app.domain }));

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="md"
      title={fixedDomain !== undefined ? t("backups.createDialog.titleFor", { domain: fixedDomain }) : t("backups.createDialog.title")}
      description={t("backups.createDialog.description")}
      footer={
        <>
          <Button disabled={create.isPending} onClick={() => close(false)}>
            {t("backups.common.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={create.isPending} disabled={target.trim() === ""}>
            {t("backups.createDialog.submit")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="flex flex-col gap-5">
        {fixedDomain === undefined ? (
          <Field label={t("backups.fields.application")} nativeLabel={false}>
            <Select
              aria-label={t("backups.fields.application")}
              value={domain}
              onValueChange={setDomain}
              placeholder={apps.isPending ? t("backups.fields.loadingApplications") : t("backups.fields.chooseApplication")}
              options={domainOptions}
              disabled={apps.isPending || domainOptions.length === 0}
            />
          </Field>
        ) : null}

        <fieldset className="flex flex-col gap-3">
          <legend className="mb-1 text-13 font-medium text-fg">{t("backups.createDialog.includeLegend")}</legend>
          <p className="text-13 text-fg-muted">{t("backups.createDialog.filesAlways")}</p>
          <Checkbox
            checked={includeEnv}
            onCheckedChange={setIncludeEnv}
            label={t("backups.createDialog.envFiles.label")}
            description={t("backups.createDialog.envFiles.description")}
          />
          <Checkbox
            checked={includeDatabase}
            onCheckedChange={setIncludeDatabase}
            label={t("backups.createDialog.databases.label")}
            description={t("backups.createDialog.databases.description")}
          />
          {includeDatabase ? (
            <Field label={t("backups.createDialog.redisMethodLabel")} nativeLabel={false} description={t("backups.createDialog.redisMethodDescription")} className="pl-6">
              <Select aria-label={t("backups.createDialog.redisMethodLabel")} value={redisMethod} onValueChange={setRedisMethod} options={[
                { value: "rdb", label: t("backups.createDialog.redisRdb") },
                { value: "aof", label: t("backups.createDialog.redisAof") },
              ]} />
            </Field>
          ) : null}
        </fieldset>

        <Field label={t("backups.createDialog.descriptionLabel")} optional description={t("backups.createDialog.descriptionHelp")}>
          <Input value={description} onValueChange={setDescription} placeholder={t("backups.createDialog.descriptionPlaceholder")} autoComplete="off" />
        </Field>

        <Disclosure label={t("backups.createDialog.moreOptions")}>
          <div className="flex flex-col gap-5">
            <fieldset className="flex flex-col gap-3">
              <legend className="mb-1 text-13 font-medium text-fg">{t("backups.createDialog.alsoLegend")}</legend>
              <Checkbox
                checked={includeDockerVolumes}
                onCheckedChange={setIncludeDockerVolumes}
                label={t("backups.createDialog.dockerVolumes.label")}
                description={t("backups.createDialog.dockerVolumes.description")}
              />
              <Checkbox
                checked={includeNodeModules}
                onCheckedChange={setIncludeNodeModules}
                label={t("backups.createDialog.nodeModules.label")}
                description={t("backups.createDialog.nodeModules.description")}
              />
              <Checkbox
                checked={includeBuild}
                onCheckedChange={setIncludeBuild}
                label={t("backups.createDialog.buildArtefacts.label")}
                description={t("backups.createDialog.buildArtefacts.description")}
              />
            </fieldset>
            <Field label={t("backups.createDialog.tagsLabel")} optional description={t("backups.createDialog.tagsDescription")}>
              <Input value={tags} onValueChange={setTags} placeholder={t("backups.createDialog.tagsPlaceholder")} autoComplete="off" />
            </Field>
          </div>
        </Disclosure>

        {create.isError ? <ErrorBlock live compact error={create.error} title={t("backups.createDialog.error")} /> : null}
      </form>
    </Dialog>
  );
}
