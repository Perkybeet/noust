import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FileKey, Pencil, Play } from "lucide-react";
import { useState } from "react";

import { removeSamlProvider, samlKeys, samlProvidersQuery, testSamlProvider } from "../../../api/queries/saml";
import type { SamlProvider, SamlTest } from "../../../api/queries/saml";
import { ErrorBlock, QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { RowAction } from "../RowAction";
import { TestResultDialog } from "./ProvidersSection";
import { SamlDrawer } from "./SamlDrawer";

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

function columnsFor(t: T): Column<SamlProvider>[] {
  return [
    {
      id: "provider",
      header: t("samlAdmin.column.provider"),
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
      header: t("samlAdmin.column.state"),
      card: "status",
      width: "w-32",
      cell: (provider) => (
        <StatusPill
          state={provider.enabled ? "running" : "stopped"}
          label={provider.enabled ? t("samlAdmin.state.enabled") : t("samlAdmin.state.disabled")}
          appearance="inline"
          size="sm"
        />
      ),
    },
    {
      id: "entity",
      header: t("samlAdmin.column.entity"),
      hideBelow: "md",
      cell: (provider) => <Mono tone="default">{provider.idp_entity_id}</Mono>,
    },
    {
      id: "certificate",
      header: t("samlAdmin.column.certificate"),
      hideBelow: "lg",
      cell: (provider) => {
        const until = soonest(provider);
        if (until === null) return <EmptyCell reason={t("samlAdmin.certificate.none")} />;
        // A certificate that has expired signs nothing anyone will accept: said as a state, not only a date.
        if (until * 1000 < Date.now()) return <StatusPill state="failed" label={t("samlAdmin.certificate.expired")} appearance="inline" size="sm" />;
        return <RelativeTime value={until} />;
      },
    },
    {
      id: "sign-up",
      header: t("samlAdmin.column.signUp"),
      hideBelow: "lg",
      cell: (provider) => <span className="text-13 text-fg-muted">{signUpWords(t, provider.provisioning)}</span>,
    },
  ];
}

/**
 * Settings > Single sign-on > SAML providers: the SAML 2.0 identity providers people sign in
 * with. The response must be signed by a certificate given here and the account's own second
 * factor still follows, which is said in the section's first sentence. A console reached over
 * plain HTTP is told why no SAML sign-in can finish there.
 */
export function SamlSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery(samlProvidersQuery());
  const [editing, setEditing] = useState<SamlProvider | "new" | null>(null);
  const [removing, setRemoving] = useState<SamlProvider | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [tested, setTested] = useState<{ name: string; result: SamlTest } | null>(null);
  const [testError, setTestError] = useState<unknown>(null);

  const runTest = async (name: string): Promise<void> => {
    setTesting(name);
    setTestError(null);
    try {
      setTested({ name, result: await testSamlProvider(name) });
    } catch (error) {
      setTestError(error);
    } finally {
      setTesting(null);
    }
  };

  const columns = columnsFor(t);
  const add = (
    <Button variant="secondary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setEditing("new")}>
      {t("samlAdmin.add")}
    </Button>
  );
  const plainHttp = query.data !== undefined && query.data.providers.length > 0 && !query.data.secure;

  return (
    <Section title={t("samlAdmin.title")} description={t("samlAdmin.description")} actions={query.data !== undefined && query.data.providers.length > 0 ? add : undefined}>
      {plainHttp ? (
        <Notice tone="warning" title={t("samlAdmin.plainHttpTitle")}>
          {t("samlAdmin.plainHttp")}
        </Notice>
      ) : null}
      {testError !== null ? <ErrorBlock live compact error={testError} title={t("ssoAdmin.providers.testResult.failed")} /> : null}
      <QueryState
        query={query}
        label={t("samlAdmin.loadingLabel")}
        skeleton={<DataTable caption={t("samlAdmin.tableCaption")} columns={columns} rows={[]} getRowId={(provider) => provider.name} loading mobile="cards" skeletonRows={1} />}
        isEmpty={(data) => data.providers.length === 0}
        empty={
          <EmptyState
            variant="firstUse"
            level={3}
            icon={<FileKey />}
            title={t("samlAdmin.emptyTitle")}
            description={t("samlAdmin.emptyDescription")}
            action={add}
            command="noust auth saml set corp --metadata-file idp-metadata.xml"
          />
        }
      >
        {(data) => (
          <DataTable
            caption={t("samlAdmin.tableCaption")}
            columns={columns}
            rows={data.providers}
            getRowId={(provider) => provider.name}
            mobile="cards"
            rowActions={(provider) => (
              <span className="flex items-center gap-1">
                <RowAction
                  label={t("samlAdmin.testLabel", { name: provider.name })}
                  text={t("samlAdmin.test")}
                  icon={<Play />}
                  loading={testing === provider.name}
                  onClick={() => void runTest(provider.name)}
                />
                <RowAction
                  label={t("samlAdmin.editLabel", { name: provider.name })}
                  text={t("samlAdmin.edit")}
                  icon={<Pencil />}
                  onClick={() => {
                    setEditing(provider);
                  }}
                />
                <RowAction
                  label={t("samlAdmin.removeLabel", { name: provider.name })}
                  text={t("samlAdmin.remove")}
                  icon={<ICONS.delete />}
                  onClick={() => {
                    setRemoving(provider);
                  }}
                />
              </span>
            )}
          />
        )}
      </QueryState>

      {editing !== null ? (
        <SamlDrawer
          provider={editing === "new" ? null : editing}
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
          title={t("samlAdmin.removeDialog.title", { name: removing.name })}
          description={t("samlAdmin.removeDialog.description")}
          actionLabel={t("samlAdmin.removeDialog.action")}
          destructive
          onConfirm={async () => {
            const gone = await removeSamlProvider(removing.name);
            toast.success(t("samlAdmin.removeDialog.toast", { name: gone.removed }));
            void queryClient.invalidateQueries({ queryKey: samlKeys.all });
          }}
        />
      ) : null}
    </Section>
  );
}
