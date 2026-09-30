import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import type { App } from "../../api/queries/apps";
import { deploymentsQuery } from "../../api/queries/deployments";
import { RelativeTime } from "../../components/page/RelativeTime";
import { buttonClassName } from "../../components/ui/Button";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import type { Deployment, LastDeployment } from "../apps/data";
import { deployMoment } from "../apps/data";

/** How many deploys the app's pages read at once: the overview's dots share the same request. */
export const RECENT_APP_DEPLOYS = 5;

/** The newest deploy, as the history has it (with its error) or as the app carries it. */
type Newest = (Pick<Deployment, "id" | "status" | "finished_at"> & { started_at?: string | null; error?: string | null }) | LastDeployment;

/** What is wrong with an app, if anything, in the order an operator should hear about it. */
export type AppCondition =
  | { kind: "failed"; lastDeploy: Newest | null }
  | { kind: "noAnswer"; port: number | null }
  | { kind: "restarting" }
  | { kind: "deployFailed"; deploy: Newest };

/**
 * Whether the app is broken, and how. Its service first (failed, not answering, or restarting
 * over and over), then a failed last deploy while an older version still serves. A stopped app
 * is not broken: operators stop apps on purpose. A job running on the app is its own news, said
 * by its progress, so the caller asks only when none is.
 */
export function appCondition(app: Pick<App, "status" | "port">, newest: Newest | null): AppCondition | null {
  const word = app.status.trim().toLowerCase().replace(" ", "_");
  const deployFailed = newest?.status === "failed" ? newest : null;
  if (word === "failed") return { kind: "failed", lastDeploy: deployFailed };
  if (word === "no_answer") return { kind: "noAnswer", port: app.port ?? null };
  if (word === "restarting") return { kind: "restarting" };
  if (deployFailed !== null) return { kind: "deployFailed", deploy: deployFailed };
  return null;
}

/** The first line of a deploy's error: what the operator reads first, verbatim. */
export function firstLine(error: string | null | undefined): string | null {
  return (
    error
      ?.split("\n")
      .map((part) => part.trim())
      .find((part) => part !== "") ?? null
  );
}

function errorOf(deploy: Newest): string | null {
  return "error" in deploy ? firstLine(deploy.error) : null;
}

/** What the banner says of an app, and whether that is known yet. */
export interface AppConditionState {
  condition: AppCondition | null;
  /**
   * True once the app and its newest deploys have been read (or could not be: the app's own
   * record of its last deploy stands in). Until then the page does not know whether a banner
   * goes above its tabs, so it holds them rather than push them down when one arrives.
   */
  known: boolean;
}

/**
 * The app's condition, from the app and its newest deploys (the banner quotes the failed
 * deploy's error, which only the history carries). The deploys are asked for at once, beside
 * the app, and share their request with the overview's dots.
 */
export function useAppCondition(domain: string, app: App | undefined): AppConditionState {
  const deploys = useQuery(deploymentsQuery({ domain, limit: RECENT_APP_DEPLOYS }));
  if (app === undefined) return { condition: null, known: false };
  const newest = deploys.data?.items[0] ?? app.last_deployment ?? null;
  return { condition: appCondition(app, newest), known: !deploys.isPending };
}

/**
 * The status banner between an app's header and its tabs, on every tab: what is wrong in a
 * sentence, what the system said when there is a line of it, and the one place to go next.
 * Not a live region: it describes the state the page opened on; a change of state is announced
 * by the header.
 */
export function AppBanner({ domain, condition, onDiagnose = false }: { domain: string; condition: AppCondition; onDiagnose?: boolean }) {
  const t = useT();
  // On the Diagnose tab the way to diagnose is the page itself.
  const diagnose = onDiagnose ? undefined : (
    <Link to="/apps/$domain/diagnose" params={{ domain }} className={buttonClassName("secondary", "sm")}>
      {t("appPages.banner.diagnose")}
    </Link>
  );

  switch (condition.kind) {
    case "failed": {
      const line = condition.lastDeploy === null ? null : errorOf(condition.lastDeploy);
      return (
        <Notice variant="banner" tone="error" title={t("appPages.banner.failedTitle")} {...(diagnose !== undefined ? { action: diagnose } : {})}>
          {condition.lastDeploy === null
            ? t("appPages.banner.failedBody")
            : t.rich("appPages.banner.failedAfterDeploy", { time: <RelativeTime value={deployMoment(condition.lastDeploy)} /> })}
          {line !== null ? (
            <span className="mt-1 block min-w-0">
              <Mono tone="muted" truncate>
                {line}
              </Mono>
            </span>
          ) : null}
        </Notice>
      );
    }
    case "noAnswer":
      return (
        <Notice
          variant="banner"
          tone="error"
          title={condition.port === null ? t("appPages.banner.noAnswerTitleNoPort") : t("appPages.banner.noAnswerTitle", { port: String(condition.port) })}
          {...(diagnose !== undefined ? { action: diagnose } : {})}
        >
          {t("appPages.banner.noAnswerBody")}
        </Notice>
      );
    case "restarting":
      return (
        <Notice
          variant="banner"
          tone="warning"
          title={t("appPages.banner.restartingTitle")}
          action={
            <Link to="/apps/$domain/logs" params={{ domain }} className={buttonClassName("secondary", "sm")}>
              {t("appPages.banner.viewLogs")}
            </Link>
          }
        >
          {t("appPages.banner.restartingBody")}
        </Notice>
      );
    case "deployFailed": {
      const line = errorOf(condition.deploy);
      return (
        <Notice
          variant="banner"
          tone="warning"
          title={t.rich("appPages.banner.deployFailedTitle", { time: <RelativeTime value={deployMoment(condition.deploy)} /> })}
          action={
            <Link
              to="/apps/$domain/deployments/$id"
              params={{ domain, id: String(condition.deploy.id) }}
              className={buttonClassName("secondary", "sm")}
            >
              {t("appPages.banner.viewLog")}
            </Link>
          }
        >
          {t("appPages.banner.deployFailedBody")}
          {line !== null ? (
            <span className="mt-1 block min-w-0">
              <Mono tone="muted" truncate>
                {line}
              </Mono>
            </span>
          ) : null}
        </Notice>
      );
    }
  }
}
