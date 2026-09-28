import { useQuery } from "@tanstack/react-query";
import { Database, Plus } from "lucide-react";
import { useMemo, useState } from "react";

import { databasesQuery, enginesQuery } from "../../api/queries/databases";
import { PageHeader } from "../../app/PageHeader";
import { CommandHint } from "../../components/page/CommandHint";
import { QueryState } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { CreateDatabaseDialog } from "./CreateDatabaseDialog";
import { DatabasesTable } from "./DatabasesTable";
import { engineLabel } from "./data";
import { EnginesStrip } from "./EnginesStrip";
import { UsersPanel } from "./UsersPanel";

const ALL = "all";

/** Databases, users and engines across the machine (D8: engines strip, then tables, not cards). */
export function DatabasesPage() {
  const t = useT();
  const engines = useQuery(enginesQuery());
  const [filter, setFilter] = useState<string>(ALL);
  const databases = useQuery(databasesQuery(filter === ALL ? null : filter));

  const runnable = useMemo(() => engines.data?.engines.filter((item) => item.installed && item.running) ?? [], [engines.data]);
  const [usersEngine, setUsersEngine] = useState<string | null>(null);
  const activeUsersEngine = usersEngine ?? runnable[0]?.name ?? "";

  return (
    <>
      <PageHeader title={t("nav.databases.label")} description={t("databases.page.description")} />
      <div className="flex flex-col gap-8">
        <EnginesStrip />

        <Section
          title={t("nav.databases.label")}
          actions={
            <>
              <Select
                aria-label={t("databases.page.filterByEngine")}
                size="sm"
                value={filter}
                onValueChange={setFilter}
                options={[
                  { value: ALL, label: t("databases.page.everyEngine") },
                  ...(engines.data?.engines ?? []).map((item) => ({ value: item.name, label: engineLabel(item.name) })),
                ]}
              />
              <CreateDatabaseDialog
                engines={runnable}
                trigger={
                  <Button size="sm" variant="primary" icon={<Plus aria-hidden="true" />} disabled={runnable.length === 0}>
                    {t("databases.page.newDatabase")}
                  </Button>
                }
              />
            </>
          }
        >
          <QueryState
            query={databases}
            label={t("databases.queryLabels.databases")}
            // The table itself with placeholder rows, and the hint under it: the loaded shape.
            skeleton={
              <div className="flex flex-col gap-3">
                <DatabasesTable databases={[]} caption={t("nav.databases.label")} loading />
                <CommandHint command="wasm db list" label={t("databases.fromTerminal")} />
              </div>
            }
            isEmpty={(data) => data.databases.length === 0}
            empty={
              <EmptyState
                icon={<Database />}
                title={t("databases.page.emptyTitle")}
                description={t("databases.page.emptyDescription")}
                action={
                  <CreateDatabaseDialog
                    engines={runnable}
                    trigger={
                      <Button variant="primary" icon={<Plus aria-hidden="true" />} disabled={runnable.length === 0}>
                        {t("databases.page.newDatabase")}
                      </Button>
                    }
                  />
                }
                command="wasm db create"
              />
            }
          >
            {(data) => (
              <div className="flex flex-col gap-3">
                <DatabasesTable
                  databases={data.databases}
                  caption={filter === ALL ? t("nav.databases.label") : t("databases.page.databasesOn", { engine: engineLabel(filter) })}
                />
                <CommandHint command="wasm db list" label={t("databases.fromTerminal")} />
              </div>
            )}
          </QueryState>
        </Section>

        <UsersPanel
          engines={engines.data?.engines ?? []}
          loading={engines.isPending}
          engine={activeUsersEngine}
          onEngineChange={setUsersEngine}
        />
      </div>
    </>
  );
}
