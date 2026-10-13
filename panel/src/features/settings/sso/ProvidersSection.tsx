import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Pencil, Play } from "lucide-react";
import { useState } from "react";

import { removeProvider, ssoAdminKeys, ssoConfigQuery, testProvider } from "../../../api/queries/ssoAdmin";
import type { SsoProviderInfo, SsoTestResult } from "../../../api/queries/ssoAdmin";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock, QueryState } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { CopyValue } from "../../../components/ui/CopyValue";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Dialog } from "../../../components/ui/Dialog";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { StatusPill } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { RowAction } from "../RowAction";
import { SettingsPrimaryAction } from "../SettingsShell";
import { ProviderDrawer } from "./ProviderDrawer";

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

function columnsFor(t: T): Column<SsoProviderInfo>[] {
  return [
    {
      id: "provider",
      header: t("ssoAdmin.providers.column.provider"),
      card: "title",
      cell: (provider) => (
        <span className="flex min-w-0 flex-col items-start gap-0.5">
          <span className="text-13 font-medium text-fg">{provider.label}</span>
          <Mono tone="faint">{provider.name}</Mono>
        </span>
      ),
    },
    {
      id: "state",
      header: t("ssoAdmin.providers.column.state"),
      card: "status",
      width: "w-32",
      cell: (provider) => (
        <StatusPill
          state={provider.enabled ? "running" : "stopped"}
          label={provider.enabled ? t("ssoAdmin.providers.state.enabled") : t("ssoAdmin.providers.state.disabled")}
          appearance="inline"
          size="sm"
        />
      ),
    },
    {
      id: "issuer",
      header: t("ssoAdmin.providers.column.issuer"),
      hideBelow: "md",
      cell: (provider) => <Mono tone="default">{provider.issuer}</Mono>,
    },
    {
      id: "sign-up",
      header: t("ssoAdmin.providers.column.signUp"),
      hideBelow: "lg",
      cell: (provider) => <span className="text-13 text-fg-muted">{signUpWords(t, provider.provisioning)}</span>,
    },
    {
      id: "secret",
      header: t("ssoAdmin.providers.column.secret"),
      hideBelow: "lg",
      cell: (provider) =>
        provider.client_secret_set ? (
          <span className="text-13 text-fg-muted">{t("ssoAdmin.providers.secret.kept")}</span>
        ) : (
          <EmptyCell reason={t("ssoAdmin.providers.secret.none")} />
        ),
    },
  ];
}

/** What the provider answered to the test, check by check, in its own words. */
function TestResultDialog({ name, result, onClose }: { name: string; result: SsoTestResult; onClose: () => void }) {
  const t = useT();
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      size="md"
      title={t("ssoAdmin.providers.testResult.title", { name })}
      description={t("ssoAdmin.providers.testResult.notice")}
      footer={<Button onClick={onClose}>{t("ssoAdmin.providers.testResult.close")}</Button>}
    >
      <div className="flex flex-col gap-4">
        <Notice tone={result.ok ? "success" : "error"} live>
          {result.ok ? t("ssoAdmin.providers.testResult.passed") : t("ssoAdmin.providers.testResult.failed")}
        </Notice>
        <ul className="flex flex-col gap-3">
          {result.checks.map((check) => (
            <li key={check.name} className="flex min-w-0 flex-col gap-1">
              <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <Mono tone="default">{check.name}</Mono>
                <StatusPill
                  state={check.ok ? "running" : "failed"}
                  label={check.ok ? t("ssoAdmin.providers.testResult.checkPassed") : t("ssoAdmin.providers.testResult.checkFailed")}
                  appearance="inline"
                  size="sm"
                />
              </span>
              <SystemOutput label={t("ssoAdmin.providers.testResult.checkOutput", { check: check.name })}>{check.detail}</SystemOutput>
            </li>
          ))}
        </ul>
      </div>
    </Dialog>
  );
}

/**
 * The identity providers: a table of what is configured, with the address to register at each,
 * adding, changing, testing and removing. The test reads the provider's discovery document and
 * keys and shows each check as the server worded it; it signs nobody in.
 */
export function ProvidersSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery(ssoConfigQuery());
  const [editing, setEditing] = useState<SsoProviderInfo | "new" | null>(null);
  const [removing, setRemoving] = useState<SsoProviderInfo | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [tested, setTested] = useState<{ name: string; result: SsoTestResult } | null>(null);
  const [testError, setTestError] = useState<unknown>(null);

  const runTest = async (name: string): Promise<void> => {
    setTesting(name);
    setTestError(null);
    try {
      setTested({ name, result: await testProvider(name) });
    } catch (error) {
      setTestError(error);
    } finally {
      setTesting(null);
    }
  };

  const columns = columnsFor(t);
  const add = (
    <Button variant="primary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setEditing("new")}>
      {t("ssoAdmin.providers.addProvider")}
    </Button>
  );

  return (
    <Section title={t("ssoAdmin.providers.title")} description={t("ssoAdmin.providers.description")}>
      {query.data !== undefined && query.data.providers.length > 0 ? <SettingsPrimaryAction>{add}</SettingsPrimaryAction> : null}
      {testError !== null ? <ErrorBlock live compact error={testError} title={t("ssoAdmin.providers.testResult.failed")} /> : null}
      <QueryState
        query={query}
        label={t("ssoAdmin.loadingLabel")}
        skeleton={<DataTable caption={t("ssoAdmin.providers.tableCaption")} columns={columns} rows={[]} getRowId={(provider) => provider.name} loading mobile="cards" skeletonRows={2} />}
        isEmpty={(data) => data.providers.length === 0}
        empty={
          <EmptyState
            variant="firstUse"
            level={3}
            icon={<KeyRound />}
            title={t("ssoAdmin.providers.emptyTitle")}
            description={t("ssoAdmin.providers.emptyDescription")}
            action={add}
            command={t("ssoAdmin.commandProviders")}
          />
        }
      >
        {(data) => (
          <div className="flex flex-col gap-4">
            <DataTable
              caption={t("ssoAdmin.providers.tableCaption")}
              columns={columns}
              rows={data.providers}
              getRowId={(provider) => provider.name}
              mobile="cards"
              rowActions={(provider) => (
                <span className="flex items-center gap-1">
                  <RowAction
                    label={t("ssoAdmin.providers.testLabel", { name: provider.name })}
                    text={t("ssoAdmin.providers.test")}
                    icon={<Play />}
                    loading={testing === provider.name}
                    onClick={() => void runTest(provider.name)}
                  />
                  <RowAction
                    label={t("ssoAdmin.providers.editLabel", { name: provider.name })}
                    text={t("ssoAdmin.providers.edit")}
                    icon={<Pencil />}
                    onClick={() => {
                      setEditing(provider);
                    }}
                  />
                  <RowAction
                    label={t("ssoAdmin.providers.removeLabel", { name: provider.name })}
                    text={t("ssoAdmin.providers.remove")}
                    icon={<ICONS.delete />}
                    onClick={() => {
                      setRemoving(provider);
                    }}
                  />
                </span>
              )}
            />
            <div className="flex flex-col gap-2">
              <p className="text-13 font-medium text-fg">{t("ssoAdmin.providers.redirect.title")}</p>
              <p className="max-w-measure text-13 text-fg-muted">{t("ssoAdmin.providers.redirect.description")}</p>
              <CopyValue value={data.redirect_uri} label={t("ssoAdmin.providers.redirect.copy")} name={t("ssoAdmin.providers.redirect.name")} />
            </div>
            <CommandHint command={t("ssoAdmin.commandProviders")} label={t("ssoAdmin.command")} />
          </div>
        )}
      </QueryState>

      {editing !== null && query.data !== undefined ? (
        <ProviderDrawer
          provider={editing === "new" ? null : editing}
          redirectUri={query.data.redirect_uri}
          onClose={() => {
            setEditing(null);
          }}
        />
      ) : null}
      {tested !== null ? (
        <TestResultDialog
          name={tested.name}
          result={tested.result}
          onClose={() => {
            setTested(null);
          }}
        />
      ) : null}
      {removing !== null ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(next) => {
            if (!next) setRemoving(null);
          }}
          title={t("ssoAdmin.providers.removeDialog.title", { name: removing.name })}
          description={t("ssoAdmin.providers.removeDialog.description")}
          actionLabel={t("ssoAdmin.providers.removeDialog.action")}
          destructive
          onConfirm={async () => {
            const gone = await removeProvider(removing.name);
            toast.success(t("ssoAdmin.providers.removedToast", { name: gone.removed }));
            void queryClient.invalidateQueries({ queryKey: ssoAdminKeys.all });
          }}
        />
      ) : null}
    </Section>
  );
}
