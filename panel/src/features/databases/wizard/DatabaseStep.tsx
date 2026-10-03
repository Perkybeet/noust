import { useQuery } from "@tanstack/react-query";

import { enginesQuery, provisioningPlanQuery } from "../../../api/queries/databases";
import type { Engine } from "../../../api/queries/databases";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { KeyValueList, KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { ChoiceCards } from "../../new-app/ChoiceCards";
import type { CreateAppBody, Step } from "../../new-app/wizard";
import { provisionableEngines } from "../../app/database/CreateLinkDialog";
import { can, engineName, isContainer, sortEngines } from "../engines";

/** What the wizard's Database step decided: no database, or a new one on an engine. */
export interface DatabaseChoice {
  engine: string | null;
  /** Also write DB_HOST, DB_PORT, DB_NAME, DB_USER and DB_PASSWORD (Laravel, Rails). */
  extraVars: boolean;
}

export const NO_DATABASE: DatabaseChoice = { engine: null, extraVars: false };

/**
 * App types the step is not offered for: a static site runs nothing that would read the variable,
 * and a monorepo or a Docker Compose project provisions its own (the server refuses the block).
 */
export const NO_DATABASE_STEP_TYPES: ReadonlySet<string> = new Set(["static", "monorepo", "docker-compose"]);

/** The variables the extra-variables box adds (Laravel, Rails). */
const EXTRA_VARIABLES = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"] as const;

/** The variables a new database writes into the app's environment, as the server names them. */
export function databaseVariables(choice: DatabaseChoice): string[] {
  if (choice.engine === null) return [];
  return [choice.engine === "redis" ? "REDIS_URL" : "DATABASE_URL", ...(choice.extraVars ? EXTRA_VARIABLES : [])];
}

/**
 * The create request with the chosen database in it: created before the first build and linked
 * once the app exists. The variables it writes are left out of `env_vars`, where the Variables
 * step may have asked for them (a `.env.example` naming DATABASE_URL): the server refuses a
 * variable given twice rather than choose between them, and the operator chose the database.
 */
export function withDatabase(body: CreateAppBody, choice: DatabaseChoice): CreateAppBody {
  if (choice.engine === null) return body;
  const written = new Set(databaseVariables(choice));
  const env = Object.fromEntries(Object.entries(body.env_vars ?? {}).filter(([name]) => !written.has(name)));
  return { ...body, env_vars: env, database: { engine: choice.engine, extra_vars: choice.extraVars } };
}

const NONE = "none";

/**
 * The engines a new application's database can be created on: the server's own. An engine in a
 * container belongs to the stack that runs it, not to an application that does not exist yet.
 */
function newAppEngines(engines: readonly Engine[]): Engine[] {
  return provisionableEngines(engines).filter((engine) => !isContainer(engine));
}

/**
 * Whether the wizard asks about a database: when an engine that can hold one runs here. Read
 * once, quietly: a server where the engines cannot be read deploys as before, without the step.
 */
export function useDatabaseStepAvailable(): boolean {
  const engines = useQuery({ ...enginesQuery(), retry: false, staleTime: 60_000 });
  return newAppEngines(engines.data?.engines ?? []).length > 0;
}

/** The steps with Database before Deploy, when it is asked. */
export function withDatabaseStep(steps: readonly Step[], enabled: boolean): readonly Step[] {
  if (!enabled || steps.includes("database")) return steps;
  const at = steps.indexOf("deploy");
  return at === -1 ? steps : [...steps.slice(0, at), "database", ...steps.slice(at)];
}

function isSlot(engine: Engine | undefined): boolean {
  return engine !== undefined && can(engine, "keys") && !can(engine, "tables");
}

/** The Deploy step's summary row for the choice, in the operator's words. */
export function databaseSummary(t: T, choice: DatabaseChoice, engines: readonly Engine[] | undefined): KeyValueItem {
  return {
    label: t("databases.wizard.summaryLabel"),
    value: choice.engine === null ? t("databases.wizard.summaryNone") : t("databases.wizard.summaryNew", { engine: engineName(choice.engine, engines) }),
    mono: false,
    copy: false,
    ...(choice.engine !== null ? { hint: t("databases.wizard.summaryHint") } : {}),
  };
}

export interface DatabaseStepProps {
  /** The application's domain, as the Address step has it: the plan names the database after it. */
  domain: string;
  value: DatabaseChoice;
  onChange: (value: DatabaseChoice) => void;
}

/**
 * The New-application wizard's Database step, for an application of any type: none, or a new
 * database on one of the engines running here, with what will be written said before anything
 * is (the database, its account, the variable, the connection string with its password
 * masked). It goes with the create request and is created before the first build, so the first
 * build and the first start already have its connection string.
 */
export function DatabaseStep({ domain, value, onChange }: DatabaseStepProps) {
  const t = useT();
  const engines = useQuery(enginesQuery());
  const choices = newAppEngines(engines.data?.engines ?? []);
  const others = sortEngines((engines.data?.engines ?? []).filter((engine) => !isContainer(engine) && !choices.includes(engine)));
  const chosen = choices.find((engine) => engine.name === value.engine);
  const plan = useQuery({ ...provisioningPlanQuery(domain, chosen?.name ?? ""), enabled: chosen !== undefined && domain.trim() !== "" });

  if (engines.isError && engines.data === undefined) {
    return <ErrorBlock error={engines.error} title={t("databases.wizard.enginesFailed")} onRetry={() => void engines.refetch()} retrying={engines.isRefetching} />;
  }
  if (engines.data === undefined) {
    return (
      <div aria-busy="true" className="grid gap-2 sm:grid-cols-2">
        <span className="sr-only">{t("databases.wizard.loading")}</span>
        <Skeleton className="h-16 rounded-control" />
        <Skeleton className="h-16 rounded-control" />
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <ChoiceCards
        legend={t("databases.wizard.legend")}
        value={chosen?.name ?? NONE}
        onValueChange={(next) => onChange({ engine: next === NONE ? null : next, extraVars: next === NONE ? false : value.extraVars })}
        options={[
          { value: NONE, label: t("databases.wizard.none"), description: t("databases.wizard.noneDescription") },
          ...choices.map((engine) => ({
            value: engine.name,
            label: t(isSlot(engine) ? "databases.wizard.newSlot" : "databases.wizard.newOn", { engine: engine.display_name }),
            description: isSlot(engine) ? t("databases.wizard.slotDescription") : t("databases.wizard.newDescription"),
            ...(engine.version ? { badge: engine.version } : {}),
          })),
        ]}
      />
      {others.length > 0 ? (
        <p className="text-12 text-fg-muted">
          {t("databases.wizard.unavailable", { engines: others.map((engine) => engine.display_name).join(", ") })}
        </p>
      ) : null}
      {chosen !== undefined ? (
        <>
          <Card padding="sm" title={t("databases.wizard.planTitle")} level={3}>
            {plan.isError ? (
              <ErrorBlock compact error={plan.error} title={t("databases.appTab.planFailed")} />
            ) : plan.data === undefined ? (
              <KeyValueListSkeleton rows={4} />
            ) : (
              <KeyValueList
                items={[
                  { label: isSlot(chosen) ? t("databases.appTab.slot") : t("databases.fields.database"), value: plan.data.database, copy: false },
                  ...(isSlot(chosen) ? [] : [{ label: t("databases.appTab.account"), value: plan.data.username, copy: false as const }]),
                  { label: t("databases.appTab.variables"), value: [...plan.data.env_vars.slice(0, 1), ...(value.extraVars ? EXTRA_VARIABLES : [])].join(", "), copy: false },
                  { label: t("databases.connect.connectionString"), value: plan.data.url, copy: false },
                ]}
              />
            )}
          </Card>
          {plan.data?.database_exists ? (
            <Notice tone="warning" title={t("databases.wizard.existsTitle")}>
              {t.rich("databases.wizard.existsBody", { name: <Mono>{plan.data.database}</Mono> })}
            </Notice>
          ) : null}
          {!isSlot(chosen) ? (
            <Checkbox
              checked={value.extraVars}
              onCheckedChange={(checked) => onChange({ ...value, extraVars: checked })}
              label={t("databases.link.extraVars")}
              description={t("databases.link.extraVarsHelp")}
            />
          ) : null}
          <Notice title={t("databases.wizard.whenTitle")}>{t("databases.wizard.whenBody")}</Notice>
        </>
      ) : null}
    </div>
  );
}
