import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Play, Server } from "lucide-react";
import { useState } from "react";

import { directoriesQuery, ldapKeys, removeDirectory, testDirectory } from "../../../api/queries/ldap";
import type { Directory, DirectoryTest } from "../../../api/queries/ldap";
import { ErrorBlock, QueryState } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { RowAction } from "../RowAction";
import { DirectoryDrawer } from "./DirectoryDrawer";
import { TestResultDialog } from "./ProvidersSection";

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

function columnsFor(t: T): Column<Directory>[] {
  return [
    {
      id: "directory",
      header: t("ldapAdmin.column.directory"),
      card: "title",
      cell: (directory) => (
        <span className="flex min-w-0 flex-col items-start gap-0.5">
          <span className="text-13 font-medium text-fg">{directory.label}</span>
          <Mono tone="faint">{directory.name}</Mono>
        </span>
      ),
    },
    {
      id: "state",
      header: t("ldapAdmin.column.state"),
      card: "status",
      width: "w-32",
      cell: (directory) => (
        <StatusPill
          state={directory.enabled ? "running" : "stopped"}
          label={directory.enabled ? t("ldapAdmin.state.enabled") : t("ldapAdmin.state.disabled")}
          appearance="inline"
          size="sm"
        />
      ),
    },
    {
      id: "server",
      header: t("ldapAdmin.column.server"),
      hideBelow: "md",
      cell: (directory) => <Mono tone="default">{`${directory.host}:${String(directory.port)} (${directory.tls})`}</Mono>,
    },
    {
      id: "sign-up",
      header: t("ldapAdmin.column.signUp"),
      hideBelow: "lg",
      cell: (directory) => <span className="text-13 text-fg-muted">{signUpWords(t, directory.provisioning)}</span>,
    },
    {
      id: "password",
      header: t("ldapAdmin.column.password"),
      hideBelow: "lg",
      cell: (directory) =>
        directory.bind_password_set ? (
          <span className="text-13 text-fg-muted">{t("ldapAdmin.password.kept")}</span>
        ) : (
          <EmptyCell reason={t("ldapAdmin.password.anonymous")} />
        ),
    },
  ];
}

/**
 * Settings > Single sign-on > Directories: the LDAP and Active Directory servers people sign in
 * against with the name and password they have there. The directory proves the password and the
 * account's own second factor still follows, which is said in the section's first sentence.
 */
export function DirectoriesSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery(directoriesQuery());
  const [editing, setEditing] = useState<Directory | "new" | null>(null);
  const [removing, setRemoving] = useState<Directory | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [tested, setTested] = useState<{ name: string; result: DirectoryTest } | null>(null);
  const [testError, setTestError] = useState<unknown>(null);

  const runTest = async (name: string): Promise<void> => {
    setTesting(name);
    setTestError(null);
    try {
      setTested({ name, result: await testDirectory(name) });
    } catch (error) {
      setTestError(error);
    } finally {
      setTesting(null);
    }
  };

  const columns = columnsFor(t);
  const add = (
    <Button variant="secondary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setEditing("new")}>
      {t("ldapAdmin.add")}
    </Button>
  );

  return (
    <Section title={t("ldapAdmin.title")} description={t("ldapAdmin.description")} actions={query.data !== undefined && query.data.directories.length > 0 ? add : undefined}>
      {testError !== null ? <ErrorBlock live compact error={testError} title={t("ssoAdmin.providers.testResult.failed")} /> : null}
      <QueryState
        query={query}
        label={t("ldapAdmin.loadingLabel")}
        skeleton={<DataTable caption={t("ldapAdmin.tableCaption")} columns={columns} rows={[]} getRowId={(directory) => directory.name} loading mobile="cards" skeletonRows={1} />}
        isEmpty={(data) => data.directories.length === 0}
        empty={
          <EmptyState
            variant="firstUse"
            level={3}
            icon={<Server />}
            title={t("ldapAdmin.emptyTitle")}
            description={t("ldapAdmin.emptyDescription")}
            action={add}
            command="noust auth ldap set corp --host ldap.acme.test --user-base ou=people,dc=acme,dc=test"
          />
        }
      >
        {(data) => (
          <DataTable
            caption={t("ldapAdmin.tableCaption")}
            columns={columns}
            rows={data.directories}
            getRowId={(directory) => directory.name}
            mobile="cards"
            rowActions={(directory) => (
              <span className="flex items-center gap-1">
                <RowAction
                  label={t("ldapAdmin.testLabel", { name: directory.name })}
                  text={t("ldapAdmin.test")}
                  icon={<Play />}
                  loading={testing === directory.name}
                  onClick={() => void runTest(directory.name)}
                />
                <RowAction
                  label={t("ldapAdmin.editLabel", { name: directory.name })}
                  text={t("ldapAdmin.edit")}
                  icon={<Pencil />}
                  onClick={() => {
                    setEditing(directory);
                  }}
                />
                <RowAction
                  label={t("ldapAdmin.removeLabel", { name: directory.name })}
                  text={t("ldapAdmin.remove")}
                  icon={<ICONS.delete />}
                  onClick={() => {
                    setRemoving(directory);
                  }}
                />
              </span>
            )}
          />
        )}
      </QueryState>

      {editing !== null ? (
        <DirectoryDrawer
          directory={editing === "new" ? null : editing}
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
          title={t("ldapAdmin.removeDialog.title", { name: removing.name })}
          description={t("ldapAdmin.removeDialog.description")}
          actionLabel={t("ldapAdmin.removeDialog.action")}
          destructive
          onConfirm={async () => {
            const gone = await removeDirectory(removing.name);
            toast.success(t("ldapAdmin.removeDialog.toast", { name: gone.removed }));
            void queryClient.invalidateQueries({ queryKey: ldapKeys.all });
          }}
        />
      ) : null}
    </Section>
  );
}
