import { useQuery } from "@tanstack/react-query";

import {
  appsDirectoryQuery,
  backupSettingsQuery,
  configQuery,
  patchConfig,
  saveAppsDirectory,
  saveBackupSettings,
  saveSslSettings,
  saveWebSettings,
  saveWebserver,
  sslSettingsQuery,
  webSettingsQuery,
  webserverQuery,
} from "../../api/queries/config";
import type { BackupSettings, ConsoleConfig, SslSettings, WebSettings } from "../../api/queries/config";
import { machineQuery } from "../../api/queries/system";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList, KeyValueListSkeleton } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { SaveBar } from "../../components/page/SaveBar";
import { Section, Sections } from "../../components/page/Section";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import type { SelectOption } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { useSaveBar } from "../app/settings/formParts";
import { readServerIdentity, selfUpdateQuery, installMethodWords, wholeNumber } from "./GeneralSettings.model";
import type { ServerIdentity } from "./GeneralSettings.model";
import { FieldsSkeleton, FormFailure, useRefreshConfig } from "./SettingsForm";
import { useSettingsForm } from "./useSettingsForm";

const WEBSERVERS: readonly SelectOption[] = [
  { value: "nginx", label: "Nginx" },
  { value: "apache", label: "Apache" },
];

/** Every value the subsection shows, once each of its sources has answered. */
interface Loaded {
  config: ConsoleConfig;
  identity: ServerIdentity;
  appsDirectory: string;
  webserver: string;
  ssl: SslSettings;
  backup: BackupSettings;
  web: WebSettings;
}

/** A system value typed into a field: mono, no autocomplete, no spellcheck. */
const SYSTEM_VALUE = { mono: true, autoComplete: "off", autoCapitalize: "off", spellCheck: false } as const;

/**
 * The subsection as one form over seven endpoints: each group of fields is a part of the save
 * bar, saved in turn when it changed, its refusal shown beside its own fields.
 */
function GeneralForm({ loaded, hostname }: { loaded: Loaded; hostname: string | undefined }) {
  const t = useT();
  const refresh = useRefreshConfig();
  const selfUpdate = useQuery(selfUpdateQuery());
  const { identity, ssl, web } = loaded;
  const hub = identity.role === "hub";

  const name = useSettingsForm({
    server: { name: identity.name },
    names: ["name"],
    soleField: "name",
    save: async ({ name: value }) => {
      await patchConfig("server.name", value.trim());
      await refresh();
    },
  });
  const publicUrl = useSettingsForm({
    server: { public_url: identity.publicUrl },
    names: ["public_url"],
    soleField: "public_url",
    save: async ({ public_url }) => {
      await patchConfig("web.public_url", public_url.trim());
      await refresh();
    },
  });
  const address = useSettingsForm({
    server: { host: web.host, port: String(web.port) },
    names: ["host", "port"],
    save: async ({ host, port }) => {
      // session_timeout rides along unchanged: the endpoint replaces the three together.
      await saveWebSettings({ host: host.trim(), port: wholeNumber(port), session_timeout: web.session_timeout });
      await refresh();
    },
  });
  const updates = useSettingsForm({
    server: { check: identity.checkUpdates },
    names: ["check"],
    save: async ({ check }) => {
      await patchConfig("updates.check", check);
      await refresh();
    },
  });
  const appsDirectory = useSettingsForm({
    server: { apps_directory: loaded.appsDirectory },
    names: ["apps_directory"],
    soleField: "apps_directory",
    save: async ({ apps_directory }) => {
      await saveAppsDirectory({ apps_directory: apps_directory.trim() });
      await refresh();
    },
  });
  const webserver = useSettingsForm({
    server: { webserver: loaded.webserver },
    names: ["webserver"],
    soleField: "webserver",
    save: async ({ webserver: value }) => {
      await saveWebserver({ webserver: value });
      await refresh();
    },
  });
  const certificates = useSettingsForm({
    server: { email: ssl.email },
    names: ["email"],
    soleField: "email",
    save: async ({ email }) => {
      // The endpoint replaces the whole block; what this form does not show goes back as it came.
      await saveSslSettings({ enabled: ssl.enabled, provider: ssl.provider, email: email.trim() });
      await refresh();
    },
  });
  const backups = useSettingsForm({
    server: { directory: loaded.backup.directory, max_per_app: String(loaded.backup.max_per_app) },
    names: ["directory", "max_per_app"],
    save: async ({ directory, max_per_app }) => {
      await saveBackupSettings({ directory: directory.trim(), max_per_app: wholeNumber(max_per_app) });
      await refresh();
    },
  });

  const bar = useSaveBar(
    [name.part, publicUrl.part, address.part, updates.part, ...(hub ? [] : [certificates.part, appsDirectory.part, webserver.part, backups.part])],
    t("settings.general.notSaved"),
  );

  return (
    <>
      {loaded.config.writable ? null : (
        <Notice tone="warning" title={t("settings.general.notWritableTitle")}>
          {t.rich("settings.general.notWritable", { path: <Mono key="path">{loaded.config.path}</Mono> })}
        </Notice>
      )}
      <Section title={t("settings.general.server.title")} description={t("settings.general.server.description")}>
        <Card>
          <div className="flex flex-col gap-5">
            <FormFailure error={name.formError ?? publicUrl.formError ?? address.formError ?? certificates.formError ?? updates.formError} title={t("settings.general.server.failed")} />
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label={t("settings.general.server.nameLabel")} optional description={t("settings.general.server.nameDescription")} error={name.fieldErrors.name}>
                <Input
                  {...SYSTEM_VALUE}
                  maxLength={64}
                  placeholder={hostname ?? ""}
                  value={name.values?.name ?? ""}
                  onValueChange={(value: string) => {
                    name.set("name", value);
                  }}
                />
              </Field>
              <Field
                label={t("settings.general.server.publicUrlLabel")}
                optional
                description={t("settings.general.server.publicUrlDescription")}
                error={publicUrl.fieldErrors.public_url}
              >
                <Input
                  {...SYSTEM_VALUE}
                  type="url"
                  placeholder="https://console.example.com"
                  value={publicUrl.values?.public_url ?? ""}
                  onValueChange={(value: string) => {
                    publicUrl.set("public_url", value);
                  }}
                />
              </Field>
            </div>
            <div className="grid gap-5 sm:grid-cols-2">
              <div className="flex min-w-0 gap-3">
                <Field
                  label={t("settings.general.server.hostLabel")}
                  description={t("settings.general.server.hostDescription")}
                  error={address.fieldErrors.host}
                  className="flex-1"
                >
                  <Input
                    {...SYSTEM_VALUE}
                    value={address.values?.host ?? ""}
                    onValueChange={(value: string) => {
                      address.set("host", value);
                    }}
                  />
                </Field>
                <Field label={t("settings.general.server.portLabel")} error={address.fieldErrors.port} className="w-24 shrink-0">
                  <Input
                    {...SYSTEM_VALUE}
                    inputMode="numeric"
                    value={address.values?.port ?? ""}
                    onValueChange={(value: string) => {
                      address.set("port", value);
                    }}
                  />
                </Field>
              </div>
              {hub ? null : (
                <Field
                  label={t("settings.general.server.emailLabel")}
                  optional
                  description={t("settings.general.server.emailDescription")}
                  error={certificates.fieldErrors.email}
                >
                  <Input
                    type="email"
                    autoComplete="email"
                    spellCheck={false}
                    placeholder="ops@example.com"
                    value={certificates.values?.email ?? ""}
                    onValueChange={(value: string) => {
                      certificates.set("email", value);
                    }}
                  />
                </Field>
              )}
            </div>
            <Checkbox
              label={t("settings.general.server.checkUpdatesLabel")}
              description={t("settings.general.server.checkUpdatesDescription", {
                source: installMethodWords(t, selfUpdate.data?.method),
              })}
              checked={updates.values?.check ?? true}
              onCheckedChange={(next) => {
                updates.set("check", next);
              }}
            />
          </div>
        </Card>
      </Section>

      {hub ? (
        <Notice title={t("settings.general.hub.title")}>{t("settings.general.hub.description")}</Notice>
      ) : (
        <Section title={t("settings.general.applications.title")} description={t("settings.general.applications.description")}>
          <Card>
            <div className="flex flex-col gap-5">
              <FormFailure
                error={appsDirectory.formError ?? webserver.formError ?? backups.formError}
                title={t("settings.general.applications.failed")}
              />
              <div className="grid gap-5 sm:grid-cols-2">
                <Field
                  label={t("settings.general.applications.directoryLabel")}
                  description={t("settings.general.applications.directoryDescription")}
                  error={appsDirectory.fieldErrors.apps_directory}
                >
                  <Input
                    {...SYSTEM_VALUE}
                    value={appsDirectory.values?.apps_directory ?? ""}
                    onValueChange={(value: string) => {
                      appsDirectory.set("apps_directory", value);
                    }}
                  />
                </Field>
                <Field
                  label={t("settings.general.applications.webserverLabel")}
                  nativeLabel={false}
                  description={t("settings.general.applications.webserverDescription")}
                  error={webserver.fieldErrors.webserver}
                >
                  <Select
                    options={WEBSERVERS}
                    value={webserver.values?.webserver ?? null}
                    onValueChange={(value) => {
                      webserver.set("webserver", value);
                    }}
                  />
                </Field>
              </div>
              <div className="grid gap-5 sm:grid-cols-2">
                <Field label={t("settings.general.applications.backupDirectoryLabel")} optional description={t("settings.general.applications.backupDirectoryDescription")} error={backups.fieldErrors.directory}>
                  <Input
                    {...SYSTEM_VALUE}
                    value={backups.values?.directory ?? ""}
                    onValueChange={(value: string) => {
                      backups.set("directory", value);
                    }}
                  />
                </Field>
                <Field
                  label={t("settings.general.applications.backupCountLabel")}
                  description={t("settings.general.applications.backupCountDescription")}
                  error={backups.fieldErrors.max_per_app}
                  className="sm:max-w-48"
                >
                  <Input
                    {...SYSTEM_VALUE}
                    inputMode="numeric"
                    value={backups.values?.max_per_app ?? ""}
                    onValueChange={(value: string) => {
                      backups.set("max_per_app", value);
                    }}
                  />
                </Field>
              </div>
            </div>
          </Card>
        </Section>
      )}

      <Details loaded={loaded} />
      <CommandHint command="noust config show" label={t("settings.general.fromTerminal")} />
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
    </>
  );
}

/** What is set from a terminal or at installation: shown, not edited. */
function Details({ loaded }: { loaded: Loaded }) {
  const t = useT();
  const { identity, config } = loaded;
  return (
    <Section title={t("settings.general.details.title")} description={t("settings.general.details.description")}>
      <Card padding="sm">
        <KeyValueList
          items={[
            {
              label: t("settings.general.details.hooksUrl"),
              value: identity.hooksUrl === "" ? null : identity.hooksUrl,
              hint:
                identity.hooksUrl === ""
                  ? t.rich("settings.general.details.hooksUrlUnset", { command: <Mono key="command">noust web expose-hooks hooks.example.com</Mono> })
                  : undefined,
            },
            { label: t("settings.general.details.configFile"), value: config.path },
            ...(identity.serviceUser === "" ? [] : [{ label: t("settings.general.details.serviceUser"), value: identity.serviceUser }]),
          ]}
          empty={t("settings.general.details.notSet")}
        />
      </Card>
    </Section>
  );
}

/** The skeleton of the whole subsection, card for card. */
function GeneralSkeleton() {
  const t = useT();
  return (
    <div aria-busy="true" className="flex flex-col gap-8">
      <span className="sr-only">{t("settings.general.loading")}</span>
      <Section title={t("settings.general.server.title")} description={t("settings.general.server.description")}>
        <FieldsSkeleton rows={[2, 2, 1]} />
      </Section>
      <Section title={t("settings.general.applications.title")} description={t("settings.general.applications.description")}>
        <FieldsSkeleton rows={[2, 2]} />
      </Section>
      <Section title={t("settings.general.details.title")} description={t("settings.general.details.description")}>
        <Card padding="sm">
          <KeyValueListSkeleton rows={3} />
        </Card>
      </Section>
    </div>
  );
}

/**
 * Settings > General: what this server is called and where it is reached, where applications
 * live and how they are served and backed up, and what is fixed at installation. One form,
 * one save bar.
 */
export function GeneralSettings() {
  const t = useT();
  useDocumentTitle(t("settings.general.documentTitle"), 1);
  const config = useQuery(configQuery());
  const appsDirectory = useQuery(appsDirectoryQuery());
  const webserver = useQuery(webserverQuery());
  const ssl = useQuery(sslSettingsQuery());
  const backup = useQuery(backupSettingsQuery());
  const web = useQuery(webSettingsQuery());
  const machine = useQuery(machineQuery());

  const queries = [config, appsDirectory, webserver, ssl, backup, web];
  const failed = queries.find((query) => query.isError && query.data === undefined);
  if (failed !== undefined) {
    return (
      <ErrorBlock
        error={failed.error}
        title={t("settings.general.loadFailed")}
        onRetry={() => {
          for (const query of queries) if (query.isError) void query.refetch();
        }}
        retrying={queries.some((query) => query.isRefetching)}
      />
    );
  }
  if (
    config.data === undefined ||
    appsDirectory.data === undefined ||
    webserver.data === undefined ||
    ssl.data === undefined ||
    backup.data === undefined ||
    web.data === undefined
  ) {
    return <GeneralSkeleton />;
  }
  const loaded: Loaded = {
    config: config.data,
    identity: readServerIdentity(config.data.config),
    appsDirectory: appsDirectory.data.apps_directory,
    webserver: webserver.data.webserver,
    ssl: ssl.data,
    backup: backup.data,
    web: web.data,
  };
  return (
    <Sections>
      <GeneralForm loaded={loaded} hostname={machine.data?.hostname} />
    </Sections>
  );
}
