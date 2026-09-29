import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { CircleAlert, FileCog } from "lucide-react";
import type { ReactNode } from "react";

import {
  appsDirectoryQuery,
  backupSettingsQuery,
  configKeys,
  configQuery,
  saveAppsDirectory,
  saveBackupSettings,
  saveSslSettings,
  saveWebSettings,
  saveWebserver,
  sslSettingsQuery,
  webSettingsQuery,
  webserverQuery,
} from "../../api/queries/config";
import type { ConfigSection } from "../../api/queries/config";
import { useDocumentTitle } from "../../app/documentTitle";
import { LanguageSwitch } from "../../app/LanguageSwitch";
import { ErrorBlock } from "../../components/page/QueryState";
import { Sections } from "../../components/page/Section";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import type { SelectOption } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { configGetCommand, configSetCommand } from "./shell";
import { SettingsFormCard, SettingsFormSkeleton, SettingsSection } from "./SettingsForm";
import type { SkeletonField } from "./SettingsForm";
import { useSettingsForm } from "./useSettingsForm";
import type { FormValues, SettingsForm } from "./useSettingsForm";

/**
 * A whole number typed into a field, as a number; anything else is sent as typed, and the
 * server's refusal is shown beside the field. The server is the one validator.
 */
function wholeNumber(value: string): number {
  const trimmed = value.trim();
  return /^-?\d+$/.test(trimmed) ? Number(trimmed) : (trimmed as unknown as number);
}

/** The terminal form of a section: what `noust config set` would change, or how to read it. */
function commandsFor<V extends FormValues>(form: SettingsForm<V>, keys: Record<keyof V & string, string>, read: string): string[] {
  if (!form.dirty || form.values === undefined) return [configGetCommand(read)];
  const values = form.values;
  return form.changed.map((name) => configSetCommand(keys[name], values[name] ?? ""));
}

/** A section's form once its settings are loaded; the skeleton or the failure until then. */
function Loaded<D>({
  t,
  query,
  label,
  fields,
  children,
}: {
  t: T;
  query: UseQueryResult<D>;
  /** What is loading, already translated: "the applications directory". */
  label: string;
  /** The form's fields, for a placeholder of the same height. */
  fields: readonly SkeletonField[];
  children: ReactNode;
}) {
  if (query.data !== undefined) return <>{children}</>;
  if (query.isError) {
    return (
      <ErrorBlock
        error={query.error}
        title={t("settings.shared.loadFailed", { label })}
        onRetry={() => void query.refetch()}
        retrying={query.isRefetching}
      />
    );
  }
  return (
    <div aria-busy="true">
      <span className="sr-only">{t("settings.shared.loading", { label })}</span>
      <SettingsFormSkeleton fields={fields} />
    </div>
  );
}

/** After a save: the cached answer becomes what was saved, then everything refreshes. */
function useSaved() {
  const queryClient = useQueryClient();
  return (section: ConfigSection, saved: unknown, message: string, description?: string): void => {
    queryClient.setQueryData(configKeys.section(section), saved);
    void queryClient.invalidateQueries({ queryKey: configKeys.all });
    toast.success(message, description !== undefined ? { description } : undefined);
  };
}

// ---------------------------------------------------------------------------------------

function AppsDirectorySection() {
  const t = useT();
  const query = useQuery(appsDirectoryQuery());
  const saved = useSaved();
  const form = useSettingsForm({
    server: query.data ? { apps_directory: query.data.apps_directory } : undefined,
    names: ["apps_directory"],
    soleField: "apps_directory",
    save: async ({ apps_directory }) => {
      const result = await saveAppsDirectory({ apps_directory: apps_directory.trim() });
      saved("apps-directory", { apps_directory: result.apps_directory }, t("settings.general.appsDirectory.saved"));
    },
  });
  return (
    <SettingsSection
      title={t("settings.general.appsDirectory.title")}
      description={t("settings.general.appsDirectory.description")}
      commands={commandsFor(form, { apps_directory: "apps_directory" }, "apps_directory")}
    >
      <Loaded t={t} query={query} label={t("settings.general.appsDirectory.loadingLabel")} fields={[{ description: 1 }]}>
        <SettingsFormCard {...cardProps(form, t("settings.general.appsDirectory.errorTitle"))}>
          <Field
            label={t("settings.general.appsDirectory.fieldLabel")}
            description={t("settings.general.appsDirectory.fieldDescription")}
            error={form.fieldErrors.apps_directory}
          >
            <Input
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              value={form.values?.apps_directory ?? ""}
              onValueChange={(value: string) => {
                form.set("apps_directory", value);
              }}
              className="max-w-md"
            />
          </Field>
        </SettingsFormCard>
      </Loaded>
    </SettingsSection>
  );
}

const WEBSERVERS: readonly SelectOption[] = [
  { value: "nginx", label: "Nginx" },
  { value: "apache", label: "Apache" },
];

function WebserverSection() {
  const t = useT();
  const query = useQuery(webserverQuery());
  const saved = useSaved();
  const form = useSettingsForm({
    server: query.data ? { webserver: query.data.webserver } : undefined,
    names: ["webserver"],
    soleField: "webserver",
    save: async ({ webserver }) => {
      const result = await saveWebserver({ webserver });
      saved("webserver", { webserver: result.webserver }, t("settings.general.webserver.saved"));
    },
  });
  return (
    <SettingsSection
      title={t("settings.general.webserver.title")}
      description={t("settings.general.webserver.description")}
      commands={commandsFor(form, { webserver: "webserver" }, "webserver")}
    >
      <Loaded t={t} query={query} label={t("settings.general.webserver.loadingLabel")} fields={[{}]}>
        <SettingsFormCard {...cardProps(form, t("settings.general.webserver.errorTitle"))}>
          <Field label={t("settings.general.webserver.fieldLabel")} nativeLabel={false} error={form.fieldErrors.webserver}>
            <Select
              options={WEBSERVERS}
              value={form.values?.webserver ?? null}
              onValueChange={(value) => {
                form.set("webserver", value);
              }}
              className="w-56"
            />
          </Field>
        </SettingsFormCard>
      </Loaded>
    </SettingsSection>
  );
}

function CertificatesSection() {
  const t = useT();
  const query = useQuery(sslSettingsQuery());
  const saved = useSaved();
  const form = useSettingsForm({
    server: query.data ? { email: query.data.email } : undefined,
    names: ["email"],
    soleField: "email",
    save: async ({ email }) => {
      // The endpoint replaces the whole block; the two values this form does not show are
      // sent back exactly as the server gave them.
      const current = query.data ?? { enabled: true, provider: "certbot", email: "" };
      const body = { enabled: current.enabled, provider: current.provider, email: email.trim() };
      await saveSslSettings(body);
      saved("ssl", body, t("settings.general.certificates.saved"));
    },
  });
  return (
    <SettingsSection
      title={t("settings.general.certificates.title")}
      description={t.rich("settings.general.certificates.description", {
        provider: <span className="mono text-12">{query.data?.provider ?? "certbot"}</span>,
      })}
      commands={commandsFor(form, { email: "ssl.email" }, "ssl.email")}
    >
      <Loaded t={t} query={query} label={t("settings.general.certificates.loadingLabel")} fields={[{ description: 2 }]}>
        <SettingsFormCard {...cardProps(form, t("settings.general.certificates.errorTitle"))}>
          <Field
            label={t("settings.general.certificates.fieldLabel")}
            optional
            description={t("settings.general.certificates.fieldDescription")}
            error={form.fieldErrors.email}
          >
            <Input
              type="email"
              autoComplete="email"
              spellCheck={false}
              placeholder="ops@example.com"
              value={form.values?.email ?? ""}
              onValueChange={(value: string) => {
                form.set("email", value);
              }}
              className="max-w-md"
            />
          </Field>
        </SettingsFormCard>
      </Loaded>
    </SettingsSection>
  );
}

function BackupsSection() {
  const query = useQuery(backupSettingsQuery());
  const t = useT();
  const saved = useSaved();
  const form = useSettingsForm({
    server: query.data ? { directory: query.data.directory, max_per_app: String(query.data.max_per_app) } : undefined,
    names: ["directory", "max_per_app"],
    save: async ({ directory, max_per_app }) => {
      const body = { directory: directory.trim(), max_per_app: wholeNumber(max_per_app) };
      await saveBackupSettings(body);
      saved("backup", body, t("settings.general.backups.saved"));
    },
  });
  return (
    <SettingsSection
      title={t("settings.general.backups.title")}
      description={t("settings.general.backups.description")}
      commands={commandsFor(form, { directory: "backup.directory", max_per_app: "backup.max_per_app" }, "backup")}
    >
      <Loaded t={t} query={query} label={t("settings.general.backups.loadingLabel")} fields={[{}, { description: 1 }]}>
        <SettingsFormCard {...cardProps(form, t("settings.general.backups.errorTitle"))}>
          <Field label={t("settings.general.backups.directoryLabel")} error={form.fieldErrors.directory}>
            <Input
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              value={form.values?.directory ?? ""}
              onValueChange={(value: string) => {
                form.set("directory", value);
              }}
              className="max-w-md"
            />
          </Field>
          <Field
            label={t("settings.general.backups.countLabel")}
            description={t("settings.general.backups.countDescription")}
            error={form.fieldErrors.max_per_app}
          >
            <Input
              type="number"
              inputMode="numeric"
              mono
              value={form.values?.max_per_app ?? ""}
              onValueChange={(value: string) => {
                form.set("max_per_app", value);
              }}
              className="w-32"
            />
          </Field>
        </SettingsFormCard>
      </Loaded>
    </SettingsSection>
  );
}

function ConsoleAddressSection() {
  const t = useT();
  const query = useQuery(webSettingsQuery());
  const saved = useSaved();
  const form = useSettingsForm({
    server: query.data ? { host: query.data.host, port: String(query.data.port) } : undefined,
    names: ["host", "port"],
    save: async ({ host, port }) => {
      // session_timeout rides along unchanged: the endpoint replaces the three together.
      const body = { host: host.trim(), port: wholeNumber(port), session_timeout: query.data?.session_timeout ?? 3600 };
      await saveWebSettings(body);
      saved("web", body, t("settings.general.consoleAddress.saved"));
    },
  });
  return (
    <SettingsSection
      title={t("settings.general.consoleAddress.title")}
      description={t.rich("settings.general.consoleAddress.description", {
        statusCommand: <span className="mono text-12">noust status --open</span>,
        webCommand: <span className="mono text-12">noust web start --host --port</span>,
      })}
      commands={commandsFor(form, { host: "web.host", port: "web.port" }, "web.host")}
    >
      <Loaded t={t} query={query} label={t("settings.general.consoleAddress.loadingLabel")} fields={[{ inline: 2 }]}>
        <SettingsFormCard {...cardProps(form, t("settings.general.consoleAddress.errorTitle"))}>
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_8rem]">
            <Field label={t("settings.general.consoleAddress.hostLabel")} error={form.fieldErrors.host}>
              <Input
                mono
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                value={form.values?.host ?? ""}
                onValueChange={(value: string) => {
                  form.set("host", value);
                }}
              />
            </Field>
            <Field label={t("settings.general.consoleAddress.portLabel")} error={form.fieldErrors.port}>
              <Input
                type="number"
                inputMode="numeric"
                mono
                value={form.values?.port ?? ""}
                onValueChange={(value: string) => {
                  form.set("port", value);
                }}
              />
            </Field>
          </div>
        </SettingsFormCard>
      </Loaded>
    </SettingsSection>
  );
}

/** The console's language: a preference of this browser, applied at once, not a server setting. */
function LanguageSection() {
  const t = useT();
  return (
    <SettingsSection title={t("language.label")} description={t("language.description")}>
      <LanguageSwitch className="w-full max-w-64" />
    </SettingsSection>
  );
}

function cardProps<V extends FormValues>(form: SettingsForm<V>, errorTitle: string) {
  return {
    dirty: form.dirty,
    pending: form.pending,
    formError: form.formError,
    errorTitle,
    onSubmit: form.submit,
    onDiscard: form.discard,
  };
}

/** Where the settings live on disk, and whether the console can write them. */
function ConfigFileLine() {
  const t = useT();
  const { data } = useQuery(configQuery());
  if (data === undefined) {
    return (
      <div className="flex h-5 items-center">
        <Skeleton className="h-3.5 w-72" />
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-13 text-fg-muted">
      <span className="flex min-w-0 items-start gap-2">
        <FileCog aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-faint" />
        <span className="min-w-0">
          {t.rich("settings.general.configFile.savedTo", { path: <span className="mono text-12 break-all text-fg">{data.path}</span> })}
        </span>
      </span>
      {data.writable ? null : (
        <span className="flex items-center gap-1.5 text-fail">
          <CircleAlert aria-hidden="true" className="size-3.5" />
          {t("settings.general.configFile.notWritable")}
        </span>
      )}
    </div>
  );
}

/** Settings > General: how Noust lays out, serves, secures and backs up applications. */
export function GeneralSettings() {
  const t = useT();
  useDocumentTitle(t("settings.general.documentTitle"), 1);
  return (
    <Sections>
      <ConfigFileLine />
      <AppsDirectorySection />
      <WebserverSection />
      <CertificatesSection />
      <BackupsSection />
      <ConsoleAddressSection />
      <LanguageSection />
    </Sections>
  );
}
