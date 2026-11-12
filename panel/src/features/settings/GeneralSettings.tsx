import { useQuery } from "@tanstack/react-query";

import {
  appsDirectoryQuery,
  backupSettingsQuery,
  configQuery,
  deliverySettingsQuery,
  patchConfig,
  saveAppsDirectory,
  saveBackupSettings,
  saveDeliverySettings,
  saveSslSettings,
  saveWebSettings,
  saveWebserver,
  sslSettingsQuery,
  webSettingsQuery,
  webserverQuery,
} from "../../api/queries/config";
import type { BackupSettings, ConsoleConfig, DeliverySettings, SslSettings, WebSettings } from "../../api/queries/config";
import { machineQuery } from "../../api/queries/system";
import { useDocumentTitle } from "../../app/documentTitle";
import { KeyValueList, KeyValueListSkeleton } from "../../components/page/KeyValueList";
import { QueryState } from "../../components/page/QueryState";
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
import { ServerFreezeSection } from "../freeze/ServerFreeze";
import { FleetBlock } from "./fleet/FleetBlock";
import { FleetStrip } from "./fleet/FollowLine";
import { readServerIdentity, selfUpdateQuery, installMethodWords, wholeNumber } from "./GeneralSettings.model";
import type { ServerIdentity } from "./GeneralSettings.model";
import { FieldsSkeleton, FormFailure, useRefreshConfig } from "./SettingsForm";
import { useSettingsForm } from "./useSettingsForm";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

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
  delivery: DeliverySettings;
}

/**
 * Which half of what was General a page draws (item 166). The policy is what Noust decides by default for the servers
 * it manages (whether to look for new versions, the account certificates are issued to, how many backups are kept, how
 * long a deployment may take, when deployments are frozen) and stays in Settings. What is the machine's is Server: its
 * folders and how it builds (`folders`), and its name and the addresses it is reached at (`identity`).
 */
export type GeneralPart = "policy" | "folders" | "identity";

/** A system value typed into a field: mono, no autocomplete, no spellcheck. */
const SYSTEM_VALUE = { mono: true, autoComplete: "off", autoCapitalize: "off", spellCheck: false } as const;

/**
 * The subsection as one form over seven endpoints: each group of fields is a part of the save
 * bar, saved in turn when it changed, its refusal shown beside its own fields.
 */
function GeneralForm({ loaded, hostname, part }: { loaded: Loaded; hostname: string | undefined; part: GeneralPart }) {
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
  // The endpoint replaces the whole block, so what a page does not show goes back as it came.
  const backupFolder = useSettingsForm({
    server: { directory: loaded.backup.directory },
    names: ["directory"],
    soleField: "directory",
    save: async ({ directory }) => {
      await saveBackupSettings({ directory: directory.trim(), max_per_app: loaded.backup.max_per_app });
      await refresh();
    },
  });
  const backupKeep = useSettingsForm({
    server: { max_per_app: String(loaded.backup.max_per_app) },
    names: ["max_per_app"],
    soleField: "max_per_app",
    save: async ({ max_per_app }) => {
      await saveBackupSettings({ directory: loaded.backup.directory, max_per_app: wholeNumber(max_per_app) });
      await refresh();
    },
  });

  const builds = useSettingsForm({
    server: { max_concurrent_builds: String(loaded.delivery.max_concurrent_builds), queue_limit: String(loaded.delivery.queue_limit) },
    names: ["max_concurrent_builds", "queue_limit"],
    save: async ({ max_concurrent_builds, queue_limit }) => {
      await saveDeliverySettings({ max_concurrent_builds: wholeNumber(max_concurrent_builds), queue_limit: wholeNumber(queue_limit), max_minutes: loaded.delivery.max_minutes });
      await refresh();
    },
  });
  const maxMinutes = useSettingsForm({
    server: { max_minutes: String(loaded.delivery.max_minutes) },
    names: ["max_minutes"],
    soleField: "max_minutes",
    save: async ({ max_minutes }) => {
      await saveDeliverySettings({
        max_concurrent_builds: loaded.delivery.max_concurrent_builds,
        queue_limit: loaded.delivery.queue_limit,
        max_minutes: wholeNumber(max_minutes),
      });
      await refresh();
    },
  });

  const parts = {
    policy: hub ? [updates.part] : [updates.part, certificates.part, backupKeep.part, maxMinutes.part],
    identity: [name.part, publicUrl.part, address.part],
    folders: hub ? [] : [appsDirectory.part, webserver.part, backupFolder.part, builds.part],
  } as const;
  const bar = useSaveBar([...parts[part]], t("settings.general.notSaved"));

  return (
    <>
      {loaded.config.writable ? null : (
        <Notice tone="warning" title={t("settings.general.notWritableTitle")}>
          {t.rich("settings.general.notWritable", { path: <Mono key="path">{loaded.config.path}</Mono> })}
        </Notice>
      )}
      {part === "identity" ? (
        <Section title={t("settings.general.server.title")} icon={OBJECT_ICONS.server} description={t("settings.general.server.description")}>
          <Card>
            <div className="flex flex-col gap-5">
              <FormFailure error={name.formError ?? publicUrl.formError ?? address.formError} title={t("settings.general.server.failed")} />
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
                <Field label={t("settings.general.server.publicUrlLabel")} optional description={t("settings.general.server.publicUrlDescription")} error={publicUrl.fieldErrors.public_url}>
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
              <div className="flex min-w-0 gap-3 sm:max-w-md">
                <Field label={t("settings.general.server.hostLabel")} description={t("settings.general.server.hostDescription")} error={address.fieldErrors.host} className="flex-1">
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
            </div>
          </Card>
        </Section>
      ) : null}

      {part === "policy" ? (
        <>
          <FleetStrip />
          <Section title={t("settings.general.policy.title")} icon={OBJECT_ICONS.general} description={t("settings.general.policy.description")}>
            <Card>
              <div className="flex flex-col gap-5">
                <FormFailure error={updates.formError ?? certificates.formError ?? backupKeep.formError ?? maxMinutes.formError} title={t("settings.general.policy.failed")} />
                <Checkbox
                  label={t("settings.general.server.checkUpdatesLabel")}
                  description={t("settings.general.server.checkUpdatesDescription", { source: installMethodWords(t, selfUpdate.data?.method) })}
                  checked={updates.values?.check ?? true}
                  onCheckedChange={(next) => {
                    updates.set("check", next);
                  }}
                />
                {hub ? null : (
                  <>
                    <Field label={t("settings.general.server.emailLabel")} optional description={t("settings.general.server.emailDescription")} error={certificates.fieldErrors.email} className="sm:max-w-md">
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
                    <div className="grid gap-5 sm:grid-cols-2">
                      <Field label={t("settings.general.applications.backupCountLabel")} description={t("settings.general.applications.backupCountDescription")} error={backupKeep.fieldErrors.max_per_app} className="sm:max-w-48">
                        <Input
                          {...SYSTEM_VALUE}
                          inputMode="numeric"
                          value={backupKeep.values?.max_per_app ?? ""}
                          onValueChange={(value: string) => {
                            backupKeep.set("max_per_app", value);
                          }}
                        />
                      </Field>
                      <Field label={t("settings.general.applications.maxMinutesLabel")} description={t("settings.general.applications.maxMinutesDescription")} error={maxMinutes.fieldErrors.max_minutes} className="sm:max-w-48">
                        <Input
                          {...SYSTEM_VALUE}
                          inputMode="numeric"
                          value={maxMinutes.values?.max_minutes ?? ""}
                          onValueChange={(value: string) => {
                            maxMinutes.set("max_minutes", value);
                          }}
                        />
                      </Field>
                    </div>
                  </>
                )}
              </div>
            </Card>
          </Section>
          {hub ? null : <ServerFreezeSection />}
          {/* What the central decides for every server, with the servers that follow it: the policy above is this central's own. */}
          <FleetBlock resource="policy" />
        </>
      ) : null}

      {part === "folders" ? (
        hub ? (
          <div className="flex flex-col gap-1 text-13 text-fg-muted">
            <p className="font-medium text-fg">{t("settings.general.hub.title")}</p>
            <p>{t("settings.general.hub.description")}</p>
          </div>
        ) : (
          <Section title={t("settings.general.applications.title")} icon={OBJECT_ICONS.app} description={t("settings.general.applications.description")}>
            <Card>
              <div className="flex flex-col gap-5">
                <FormFailure error={appsDirectory.formError ?? webserver.formError ?? backupFolder.formError ?? builds.formError} title={t("settings.general.applications.failed")} />
                <div className="grid gap-5 sm:grid-cols-2">
                  <Field label={t("settings.general.applications.directoryLabel")} description={t("settings.general.applications.directoryDescription")} error={appsDirectory.fieldErrors.apps_directory}>
                    <Input
                      {...SYSTEM_VALUE}
                      value={appsDirectory.values?.apps_directory ?? ""}
                      onValueChange={(value: string) => {
                        appsDirectory.set("apps_directory", value);
                      }}
                    />
                  </Field>
                  <Field label={t("settings.general.applications.webserverLabel")} nativeLabel={false} description={t("settings.general.applications.webserverDescription")} error={webserver.fieldErrors.webserver}>
                    <Select
                      options={WEBSERVERS}
                      value={webserver.values?.webserver ?? null}
                      onValueChange={(value) => {
                        webserver.set("webserver", value);
                      }}
                    />
                  </Field>
                </div>
                <Field label={t("settings.general.applications.backupDirectoryLabel")} optional description={t("settings.general.applications.backupDirectoryDescription")} error={backupFolder.fieldErrors.directory} className="sm:max-w-md">
                  <Input
                    {...SYSTEM_VALUE}
                    value={backupFolder.values?.directory ?? ""}
                    onValueChange={(value: string) => {
                      backupFolder.set("directory", value);
                    }}
                  />
                </Field>
                <fieldset className="flex flex-col gap-5">
                  <legend className="mb-1 text-13 font-medium text-fg">{t("settings.general.applications.deliveryLegend")}</legend>
                  <div className="grid gap-5 sm:grid-cols-2">
                    <Field label={t("settings.general.applications.maxBuildsLabel")} description={t("settings.general.applications.maxBuildsDescription")} error={builds.fieldErrors.max_concurrent_builds}>
                      <Input
                        {...SYSTEM_VALUE}
                        inputMode="numeric"
                        value={builds.values?.max_concurrent_builds ?? ""}
                        onValueChange={(value: string) => {
                          builds.set("max_concurrent_builds", value);
                        }}
                      />
                    </Field>
                    <Field label={t("settings.general.applications.queueLimitLabel")} description={t("settings.general.applications.queueLimitDescription")} error={builds.fieldErrors.queue_limit}>
                      <Input
                        {...SYSTEM_VALUE}
                        inputMode="numeric"
                        value={builds.values?.queue_limit ?? ""}
                        onValueChange={(value: string) => {
                          builds.set("queue_limit", value);
                        }}
                      />
                    </Field>
                  </div>
                </fieldset>
              </div>
            </Card>
          </Section>
        )
      ) : null}

      {part === "folders" ? <Details loaded={loaded} /> : null}
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
function GeneralSkeleton({ part }: { part: GeneralPart }) {
  const t = useT();
  return (
    <div className="flex flex-col gap-8">
      {part === "policy" ? (
        <Section title={t("settings.general.policy.title")} icon={OBJECT_ICONS.general} description={t("settings.general.policy.description")}>
          <FieldsSkeleton rows={[1, 2]} />
        </Section>
      ) : null}
      {part === "identity" ? (
        <Section title={t("settings.general.server.title")} icon={OBJECT_ICONS.server} description={t("settings.general.server.description")}>
          <FieldsSkeleton rows={[2, 1]} />
        </Section>
      ) : null}
      {part === "folders" ? (
        <>
          <Section title={t("settings.general.applications.title")} icon={OBJECT_ICONS.app} description={t("settings.general.applications.description")}>
            <FieldsSkeleton rows={[2, 2]} />
          </Section>
          <Section title={t("settings.general.details.title")} description={t("settings.general.details.description")}>
            <Card padding="sm">
              <KeyValueListSkeleton rows={3} />
            </Card>
          </Section>
        </>
      ) : null}
    </div>
  );
}

/** The title of the page in the browser's tab: only the pages that are a page of their own set it, not the section of another. */
function PageTitle({ title }: { title: string }) {
  useDocumentTitle(title, 1);
  return null;
}

/**
 * Settings > General is the policy; Server > Folders and builds is where applications live and how they are built, and
 * Server > Machine carries the name and the addresses (item 166). One form and one save bar over the half it draws.
 */
export function GeneralSettings({ part = "policy" }: { part?: GeneralPart }) {
  const t = useT();
  const config = useQuery(configQuery());
  const appsDirectory = useQuery(appsDirectoryQuery());
  const webserver = useQuery(webserverQuery());
  const ssl = useQuery(sslSettingsQuery());
  const backup = useQuery(backupSettingsQuery());
  const web = useQuery(webSettingsQuery());
  const delivery = useQuery(deliverySettingsQuery());
  const machine = useQuery(machineQuery());

  const queries = [config, appsDirectory, webserver, ssl, backup, web, delivery];
  const failed = queries.find((query) => query.data === undefined && query.error !== null);
  const loaded: Loaded | undefined =
    config.data === undefined ||
    appsDirectory.data === undefined ||
    webserver.data === undefined ||
    ssl.data === undefined ||
    backup.data === undefined ||
    web.data === undefined ||
    delivery.data === undefined
      ? undefined
      : {
          config: config.data,
          identity: readServerIdentity(config.data.config),
          appsDirectory: appsDirectory.data.apps_directory,
          webserver: webserver.data.webserver,
          ssl: ssl.data,
          backup: backup.data,
          web: web.data,
          delivery: delivery.data,
        };
  // Seven reads make one form: it opens when all of them answered, and the first failure is the
  // one told, with a retry that asks again only for those that failed.
  const everything = {
    data: loaded,
    error: failed?.error ?? null,
    isPending: loaded === undefined && failed === undefined,
    isError: failed !== undefined,
    isRefetching: queries.some((query) => query.isRefetching),
    refetch: () => {
      for (const query of queries) if (query.isError) void query.refetch();
    },
  };
  return (
    <QueryState query={everything} label={t("settings.general.what")} skeleton={<GeneralSkeleton part={part} />}>
      {(data) => (
        <Sections>
          {part === "policy" ? <PageTitle title={t("settings.general.documentTitle")} /> : part === "folders" ? <PageTitle title={t("server.system.views.folders")} /> : null}
          <GeneralForm loaded={data} hostname={machine.data?.hostname} part={part} />
        </Sections>
      )}
    </QueryState>
  );
}
