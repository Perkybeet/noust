import { useMutation, useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { engineCatalogQuery } from "../../api/queries/databases";
import type { FlavourChoice, JobAccepted, VersionChoice } from "../../api/queries/databases";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";

export interface InstallEngineDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Preselects the first flavour of this engine that can be installed, as an engine's row does. */
  engine?: string;
  /** The installation was queued: the page follows the job, named after the flavour. */
  onQueued: (accepted: JobAccepted, name: string) => void;
}

/** A flavour as an option: blocked ones stay in the list, disabled, so the choice is complete. */
function flavourLabel(t: T, flavour: FlavourChoice): string {
  if (flavour.installable) return flavour.display_name;
  return flavour.installed ? t("databases.install.flavourInstalled", { engine: flavour.display_name }) : t("databases.install.flavourBlocked", { engine: flavour.display_name });
}

/** A version as an option: where it comes from, and whether it is the one installed by default. */
function versionLabel(t: T, version: VersionChoice): string {
  const values = { version: version.version };
  if (version.source === "distribution") {
    return version.default ? t("databases.install.versionDistributionDefault", values) : t("databases.install.versionDistribution", values);
  }
  return version.default ? t("databases.install.versionUpstreamDefault", values) : t("databases.install.versionUpstream", values);
}

/**
 * "Install engine": what this server can install, read from its catalog. One flavour
 * (PostgreSQL, MySQL, MariaDB, Redis, Valkey, MongoDB), then one of its versions, the default
 * chosen; a flavour that cannot be installed here is listed disabled with the server's reason
 * (MariaDB beside MySQL, a distribution without apt). The installation is a job the page
 * follows, so the dialog closes once it is queued.
 */
export function InstallEngineDialog({ open, onOpenChange, engine: preset, onQueued }: InstallEngineDialogProps) {
  const t = useT();
  const catalog = useQuery({ ...engineCatalogQuery(), enabled: open });
  const [flavour, setFlavour] = useState<string | null>(null);
  const [version, setVersion] = useState<string | null>(null);

  const flavours = catalog.data?.flavours ?? [];
  const installable = flavours.filter((item) => item.installable);
  const chosen =
    installable.find((item) => item.flavour === flavour) ?? installable.find((item) => preset !== undefined && item.engine === preset) ?? installable[0];
  const versions = chosen?.versions ?? [];
  const chosenVersion = versions.find((item) => item.version === version) ?? versions.find((item) => item.default) ?? versions[0];
  // What cannot be installed for a reason other than being installed already: said in full.
  const blocked = flavours.filter((item) => !item.installable && !item.installed && item.reason);

  const install = useMutation({
    mutationFn: (target: FlavourChoice) =>
      request("post", "/api/databases/engines/{engine}/install", {
        params: { engine: target.engine },
        body: { flavour: target.flavour, version: chosenVersion?.version ?? null },
      }),
    onSuccess: (accepted, target) => {
      onQueued(accepted, target.display_name);
      onOpenChange(false);
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (chosen !== undefined) install.mutate(chosen);
  };

  const formId = "install-engine-form";
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={t("databases.install.title")}
      description={t("databases.install.description")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("databases.common.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" icon={<Download aria-hidden="true" />} loading={install.isPending} disabled={chosen === undefined}>
            {chosen !== undefined ? t("databases.install.submit", { engine: chosen.display_name }) : t("databases.engines.install")}
          </Button>
        </>
      }
    >
      {catalog.isError && catalog.data === undefined ? (
        <ErrorBlock compact error={catalog.error} title={t("databases.install.catalogFailed")} onRetry={() => void catalog.refetch()} retrying={catalog.isRefetching} />
      ) : catalog.data === undefined ? (
        <LoadingRegion label={t("databases.install.loading")} className="flex flex-col gap-5">
          <Skeleton className="h-control-md w-full" />
          <Skeleton className="h-control-md w-full" />
        </LoadingRegion>
      ) : (
        <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
          <p className="text-13 text-fg-muted">{t("databases.install.distribution", { distribution: catalog.data.distribution.name })}</p>
          {chosen === undefined ? (
            <Notice title={t("databases.install.nothingTitle")}>{t("databases.install.nothing")}</Notice>
          ) : (
            <>
              <Field label={t("databases.install.engine")} nativeLabel={false}>
                <Select
                  value={chosen.flavour}
                  onValueChange={(value) => {
                    setFlavour(value);
                    setVersion(null);
                    install.reset();
                  }}
                  options={flavours.map((item) => ({ value: item.flavour, label: flavourLabel(t, item), disabled: !item.installable }))}
                />
              </Field>
              {versions.length > 0 ? (
                <Field label={t("databases.install.version")} nativeLabel={false} description={t("databases.install.versionHelp")}>
                  <Select
                    value={chosenVersion?.version ?? null}
                    onValueChange={(value) => {
                      setVersion(value);
                      install.reset();
                    }}
                    options={versions.map((item) => ({ value: item.version, label: versionLabel(t, item) }))}
                  />
                </Field>
              ) : null}
            </>
          )}
          {blocked.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              <p className="text-13 font-medium text-fg">{t("databases.install.blockedTitle")}</p>
              <ul className="flex max-w-measure flex-col gap-1 text-12 text-fg-muted">
                {blocked.map((item) => (
                  <li key={item.flavour}>
                    <span className="font-medium text-fg">{item.display_name}</span> <span translate="no">{item.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {install.isError ? (
            <ErrorBlock live compact error={install.error} title={t("databases.engines.installFailed", { engine: chosen?.display_name ?? "" })} />
          ) : null}
        </form>
      )}
    </Dialog>
  );
}
