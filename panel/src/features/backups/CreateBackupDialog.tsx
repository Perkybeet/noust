import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { ReactElement, SyntheticEvent } from "react";

import { appsQuery } from "../../api/queries/apps";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { useBackupActions } from "./useBackupActions";

export interface CreateBackupDialogProps {
  trigger: ReactElement<Record<string, unknown>>;
  /** Preselects the application; omit to let the operator choose. */
  domain?: string;
}

/** Creates a backup of an application with the full option set `CreateBackupRequest` takes. */
export function CreateBackupDialog({ trigger, domain: fixedDomain }: CreateBackupDialogProps) {
  const t = useT();
  const REDIS_METHODS = [
    { value: "rdb", label: t("backups.createDialog.redisRdb") },
    { value: "aof", label: t("backups.createDialog.redisAof") },
  ];
  const formId = useId();
  const [open, setOpen] = useState(false);
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
    setOpen(next);
    if (!next) reset();
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (domain.trim() === "") return;
    create.mutate(
      {
        domain: domain.trim(),
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
      trigger={trigger}
      size="lg"
      title={t("backups.createDialog.title")}
      description={t("backups.createDialog.description")}
      footer={
        <>
          <Button disabled={create.isPending} onClick={() => close(false)}>
            {t("backups.common.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={create.isPending} disabled={domain.trim() === ""}>
            {t("backups.createDialog.title")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
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
          <Field label={t("backups.createDialog.descriptionLabel")} optional>
            <Input value={description} onValueChange={setDescription} placeholder={t("backups.createDialog.descriptionPlaceholder")} autoComplete="off" />
          </Field>
        </div>

        <fieldset className="flex flex-col gap-2.5">
          <legend className="mb-1 text-13 font-medium text-fg">{t("backups.createDialog.includeLegend")}</legend>
          <div className="grid grid-cols-2 gap-x-4 gap-y-2.5">
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
          </div>
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-2">
          {includeDatabase ? (
            <Field label={t("backups.createDialog.redisMethodLabel")} nativeLabel={false} description={t("backups.createDialog.redisMethodDescription")}>
              <Select aria-label={t("backups.createDialog.redisMethodLabel")} value={redisMethod} onValueChange={setRedisMethod} options={REDIS_METHODS} />
            </Field>
          ) : null}
          <Field label={t("backups.createDialog.tagsLabel")} optional description={t("backups.createDialog.tagsDescription")}>
            <Input value={tags} onValueChange={setTags} placeholder={t("backups.createDialog.tagsPlaceholder")} autoComplete="off" />
          </Field>
        </div>

        {create.isError ? <ErrorBlock live compact error={create.error} title={t("backups.createDialog.error")} /> : null}
      </form>
    </Dialog>
  );
}
