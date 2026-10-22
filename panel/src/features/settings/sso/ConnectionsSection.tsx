import { FileKey, KeyRound, Pencil, Play } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { directoriesQuery, ldapKeys, removeDirectory, testDirectory } from "../../../api/queries/ldap";
import type { Directory } from "../../../api/queries/ldap";
import { removeSamlProvider, samlKeys, samlProvidersQuery, testSamlProvider } from "../../../api/queries/saml";
import type { SamlProvider } from "../../../api/queries/saml";
import { removeProvider, ssoAdminKeys, ssoConfigQuery, testProvider } from "../../../api/queries/ssoAdmin";
import type { SsoProviderInfo, SsoTestResult } from "../../../api/queries/ssoAdmin";
import { CommandHint } from "../../../components/page/CommandHint";
import { useNow } from "../../../components/page/clock";
import { ErrorBlock, QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { RowMenu } from "../../../components/page/RowMenu";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { CopyValue } from "../../../components/ui/CopyValue";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { SettingsPrimaryAction } from "../SettingsShell";
import { DirectoryDrawer } from "./DirectoryDrawer";
import { ProviderDrawer } from "./ProviderDrawer";
import { TestResultDialog } from "./TestResultDialog";
import { SamlDrawer } from "./SamlDrawer";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

type Kind = "oidc" | "saml" | "directory";

/** One way in, whichever protocol it speaks: what the single list shows and what its menu acts on. */
interface Connection {
  kind: Kind;
  name: string;
  label: string;
  enabled: boolean;
  /** Where it points: the issuer, the entity id, the directory's server. */
  detail: ReactNode;
  provisioning: string;
  source: SsoProviderInfo | SamlProvider | Directory;
}

type Editing = { kind: "oidc"; item: SsoProviderInfo | null } | { kind: "saml"; item: SamlProvider | null } | { kind: "directory"; item: Directory | null };

function signUpWords(t: T, provisioning: string): string {
  switch (provisioning) {
    case "invite":
      return t("ssoAdmin.providers.signUp.invite");
    case "auto":
      return t("ssoAdmin.providers.signUp.auto");
    default:
      return t("ssoAdmin.providers.signUp.off");
  }
}

/** The certificate that stops being good first: the one an operator has to replace next. */
function soonest(provider: SamlProvider): number | null {
  const times = provider.certificate_details.map((certificate) => certificate.not_after);
  return times.length === 0 ? null : Math.min(...times);
}

function kindWords(t: T, connection: Connection): string {
  if (connection.kind === "saml") return t("ssoAdmin.connections.kind.saml");
  if (connection.kind === "directory") return t("ssoAdmin.connections.kind.directory");
  return (connection.source as SsoProviderInfo).kind === "github" ? t("ssoAdmin.providers.kind.github") : t("ssoAdmin.connections.kind.oidc");
}

function columnsFor(t: T): Column<Connection>[] {
  return [
    {
      id: "connection",
      header: t("ssoAdmin.connections.column.connection"),
      card: "title",
      cell: (connection) => (
        <span className="flex min-w-0 flex-col items-start gap-0.5">
          <span className="text-13 font-medium text-fg">{connection.label}</span>
          <Mono tone="faint">{connection.name}</Mono>
        </span>
      ),
    },
    {
      id: "type",
      header: t("ssoAdmin.connections.column.type"),
      card: "meta",
      cell: (connection) => <Badge>{kindWords(t, connection)}</Badge>,
    },
    {
      id: "state",
      header: t("ssoAdmin.connections.column.state"),
      card: "status",
      width: "w-32",
      cell: (connection) => (
        <StatusPill
          state={connection.enabled ? "running" : "stopped"}
          label={connection.enabled ? t("ssoAdmin.providers.state.enabled") : t("ssoAdmin.providers.state.disabled")}
          appearance="inline"
          size="sm"
        />
      ),
    },
    {
      id: "details",
      header: t("ssoAdmin.connections.column.details"),
      hideBelow: "md",
      cell: (connection) => connection.detail,
    },
    {
      id: "sign-up",
      header: t("ssoAdmin.connections.column.signUp"),
      hideBelow: "lg",
      cell: (connection) => <span className="text-13 text-fg-muted">{signUpWords(t, connection.provisioning)}</span>,
    },
  ];
}

/**
 * Settings > Single sign-on > Connections: every way a person signs in through somebody else's
 * word, in one list whichever protocol it speaks (OpenID Connect, SAML, a directory), each with
 * its own drawer. Adding is one menu of three; testing, changing and removing are the row's.
 * Each provider vouches for who a person is and the account's own second factor still follows.
 */
export function ConnectionsSection() {
  const t = useT();
  const queryClient = useQueryClient();
  // Whether a certificate has expired is judged once a minute, never while drawing.
  const now = useNow(() => 60_000);
  const oidc = useQuery(ssoConfigQuery());
  const saml = useQuery(samlProvidersQuery());
  const ldap = useQuery(directoriesQuery());
  const [editing, setEditing] = useState<Editing | null>(null);
  const [removing, setRemoving] = useState<Connection | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [tested, setTested] = useState<{ name: string; result: SsoTestResult } | null>(null);
  const [testError, setTestError] = useState<unknown>(null);

  const rows: Connection[] = [
    ...(oidc.data?.providers ?? []).map(
      (provider): Connection => ({
        kind: "oidc",
        name: provider.name,
        label: provider.label,
        enabled: provider.enabled,
        detail: <Mono tone="default">{provider.issuer}</Mono>,
        provisioning: provider.provisioning,
        source: provider,
      }),
    ),
    ...(saml.data?.providers ?? []).map(
      (provider): Connection => {
        const until = soonest(provider);
        return {
          kind: "saml",
          name: provider.name,
          label: provider.label,
          enabled: provider.enabled,
          detail: (
            <span className="flex min-w-0 flex-col items-start gap-0.5">
              <Mono tone="default">{provider.idp_entity_id}</Mono>
              {/* A certificate that has expired signs nothing anyone will accept: said as a state, not only a date. */}
              {until !== null ? (
                until * 1000 < now ? (
                  <StatusPill state="failed" label={t("samlAdmin.certificate.expired")} appearance="inline" size="sm" />
                ) : (
                  <span className="text-12 text-fg-muted">
                    {t("ssoAdmin.connections.certificateUntil")} <RelativeTime value={until} />
                  </span>
                )
              ) : null}
            </span>
          ),
          provisioning: provider.provisioning,
          source: provider,
        };
      },
    ),
    ...(ldap.data?.directories ?? []).map(
      (directory): Connection => ({
        kind: "directory",
        name: directory.name,
        label: directory.label,
        enabled: directory.enabled,
        detail: <Mono tone="default">{`${directory.host}:${String(directory.port)} (${directory.tls})`}</Mono>,
        provisioning: directory.provisioning,
        source: directory,
      }),
    ),
  ];
  const pending = oidc.isPending || saml.isPending || ldap.isPending;
  const failed = [oidc, saml, ldap].find((query) => query.data === undefined && query.error !== null);
  // Three reads make one list: it is shown when all three answered, and the first failure is the
  // one told, with a retry that asks again only for those that failed.
  const list = {
    data: pending || failed !== undefined ? undefined : rows,
    error: failed?.error ?? null,
    isPending: pending,
    isError: failed !== undefined,
    isRefetching: [oidc, saml, ldap].some((query) => query.isRefetching),
    refetch: () => {
      for (const query of [oidc, saml, ldap]) if (query.isError) void query.refetch();
    },
  };
  const plainHttp = saml.data !== undefined && saml.data.providers.length > 0 && !saml.data.secure;
  const hasOidc = (oidc.data?.providers.length ?? 0) > 0;

  const runTest = async (connection: Connection): Promise<void> => {
    setTesting(connection.name);
    setTestError(null);
    try {
      const result =
        connection.kind === "oidc" ? await testProvider(connection.name) : connection.kind === "saml" ? await testSamlProvider(connection.name) : await testDirectory(connection.name);
      setTested({ name: connection.name, result });
    } catch (error) {
      setTestError(error);
    } finally {
      setTesting(null);
    }
  };

  const edit = (connection: Connection): void => {
    if (connection.kind === "oidc") setEditing({ kind: "oidc", item: connection.source as SsoProviderInfo });
    else if (connection.kind === "saml") setEditing({ kind: "saml", item: connection.source as SamlProvider });
    else setEditing({ kind: "directory", item: connection.source as Directory });
  };

  const remove = async (connection: Connection): Promise<void> => {
    if (connection.kind === "oidc") {
      await removeProvider(connection.name);
      void queryClient.invalidateQueries({ queryKey: ssoAdminKeys.all });
    } else if (connection.kind === "saml") {
      await removeSamlProvider(connection.name);
      void queryClient.invalidateQueries({ queryKey: samlKeys.all });
    } else {
      await removeDirectory(connection.name);
      void queryClient.invalidateQueries({ queryKey: ldapKeys.all });
    }
  };

  const removeWords = (connection: Connection): { title: string; description: string; action: string } =>
    connection.kind === "oidc"
      ? {
          title: t("common.words.removeNamed", { name: connection.name }),
          description: t("ssoAdmin.providers.removeDialog.description"),
          action: t("ssoAdmin.providers.removeDialog.action"),
        }
      : connection.kind === "saml"
        ? {
            title: t("common.words.removeNamedQuestion", { name: connection.name }),
            description: t("samlAdmin.removeDialog.description"),
            action: t("samlAdmin.removeDialog.action"),
          }
        : {
            title: t("common.words.removeNamedQuestion", { name: connection.name }),
            description: t("ldapAdmin.removeDialog.description"),
            action: t("ldapAdmin.removeDialog.action"),
          };

  const add = (
    <Menu
      trigger={
        <Button variant="primary" icon={<ICONS.add aria-hidden="true" />}>
          {t("ssoAdmin.connections.add")}
        </Button>
      }
    >
      <MenuItem icon={<KeyRound />} onClick={() => setEditing({ kind: "oidc", item: null })} description={t("ssoAdmin.connections.addOidcHelp")}>
        {t("ssoAdmin.connections.addOidc")}
      </MenuItem>
      <MenuItem icon={<FileKey />} onClick={() => setEditing({ kind: "saml", item: null })} description={t("ssoAdmin.connections.addSamlHelp")}>
        {t("ssoAdmin.connections.addSaml")}
      </MenuItem>
      <MenuItem icon={<OBJECT_ICONS.identity />} onClick={() => setEditing({ kind: "directory", item: null })} description={t("ssoAdmin.connections.addDirectoryHelp")}>
        {t("ssoAdmin.connections.addDirectory")}
      </MenuItem>
    </Menu>
  );
  const columns = columnsFor(t);

  return (
    <Section title={t("ssoAdmin.connections.title")} icon={OBJECT_ICONS.identity} description={t("ssoAdmin.connections.description")}>
      {!pending && rows.length > 0 ? <SettingsPrimaryAction>{add}</SettingsPrimaryAction> : null}
      {plainHttp ? (
        <Notice tone="warning" title={t("samlAdmin.plainHttpTitle")}>
          {t("samlAdmin.plainHttp")}
        </Notice>
      ) : null}
      {testError !== null ? <ErrorBlock live compact error={testError} title={t("ssoAdmin.providers.testResult.failed")} /> : null}
      <QueryState
        query={list}
        label={t("ssoAdmin.connections.what")}
        skeleton={
          <DataTable caption={t("ssoAdmin.connections.tableCaption")} columns={columns} rows={[]} getRowId={(connection) => `${connection.kind}:${connection.name}`} loading skeletonRows={2} />
        }
        isEmpty={(connections) => connections.length === 0}
        empty={
          // One line and its action, so the section is about as tall empty as it is while it loads: three
          // first-use blocks on one page would push every section under them by a screen.
          <div className="flex flex-col gap-3">
            <EmptyState variant="inline" title={t("ssoAdmin.connections.emptyTitle")} action={add} />
            <CommandHint command={t("ssoAdmin.commandProviders")} label={t("ssoAdmin.command")} />
          </div>
        }
      >
        {() => (
          <div className="flex flex-col gap-4">
            <DataTable
              caption={t("ssoAdmin.connections.tableCaption")}
              columns={columns}
              rows={rows}
              getRowId={(connection) => `${connection.kind}:${connection.name}`}
              onRowActivate={edit}
              rowActions={(connection) => (
                <RowMenu name={connection.name}>
                  <MenuItem icon={<Play />} disabled={testing === connection.name} onClick={() => void runTest(connection)}>
                    {t("ssoAdmin.providers.test")}
                  </MenuItem>
                  <MenuItem icon={<Pencil />} onClick={() => edit(connection)}>{t("common.words.edit")}</MenuItem>
                  <MenuSeparator />
                  <MenuItem icon={<ICONS.delete />} destructive onClick={() => setRemoving(connection)}>
                    {t("common.words.remove")}
                  </MenuItem>
                </RowMenu>
              )}
            />
            {hasOidc && oidc.data !== undefined ? (
              <div className="flex flex-col gap-2">
                <p className="text-13 font-medium text-fg">{t("ssoAdmin.providers.redirect.title")}</p>
                <p className="max-w-measure text-13 text-fg-muted">{t("ssoAdmin.providers.redirect.description")}</p>
                <CopyValue value={oidc.data.redirect_uri} label={t("ssoAdmin.providers.redirect.copy")} name={t("ssoAdmin.providers.redirect.name")} />
              </div>
            ) : null}
            <CommandHint command={t("ssoAdmin.commandProviders")} label={t("ssoAdmin.command")} />
          </div>
        )}
      </QueryState>

      {editing?.kind === "oidc" && oidc.data !== undefined ? (
        <ProviderDrawer provider={editing.item} redirectUri={oidc.data.redirect_uri} onClose={() => setEditing(null)} />
      ) : null}
      {editing?.kind === "saml" ? <SamlDrawer provider={editing.item} onClose={() => setEditing(null)} /> : null}
      {editing?.kind === "directory" ? <DirectoryDrawer directory={editing.item} onClose={() => setEditing(null)} /> : null}
      {tested !== null ? <TestResultDialog name={tested.name} result={tested.result} onClose={() => setTested(null)} /> : null}
      {removing !== null ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(next) => {
            if (!next) setRemoving(null);
          }}
          title={removeWords(removing).title}
          description={removeWords(removing).description}
          actionLabel={removeWords(removing).action}
          destructive
          onConfirm={() => remove(removing)}
        />
      ) : null}
    </Section>
  );
}
