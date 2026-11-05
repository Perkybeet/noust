import type { DbServer } from "../../api/queries/databases";
import { GuardedButton } from "../../components/page/permissions";
import { VERB_ICONS } from "../../components/ui/objectIcons";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { serverWithPlace } from "./servers";
import type { ServerActions } from "./servers/useServerActions";

export interface EngineStoppedProps {
  server: DbServer;
  /** What starts it: the engines' own actions, so a container's start is followed as the job it is. */
  actions: Pick<ServerActions, "start" | "starting" | "busy">;
  /** Whether the engine is stopped or only changing (being made, upgraded or removed): the second has nothing to press. */
  changing?: boolean;
  className?: string;
}

/**
 * What a database says when the engine it runs on does not answer: that the engine is stopped, what that takes with it
 * and what it leaves, and "Start the engine" as the one action of the page. Every section that needs the engine draws
 * this in place of what it would have read, so a stopped engine is never a skeleton that does not end, an error with the
 * system's own words, or a section that looks fine and fails.
 */
export function EngineStopped({ server, actions, changing = false, className }: EngineStoppedProps) {
  const t = useT();
  const engine = serverWithPlace(t, server);
  return (
    <section
      data-slot="engine-stopped"
      aria-label={t("databases.stopped.label")}
      className={cx("flex flex-wrap items-center gap-x-4 gap-y-3 rounded-card border border-idle-border bg-idle-soft px-5 py-4", className)}
    >
      <StatusPill state={changing ? "deploying" : "stopped"} label={changing ? t("databases.state.changing") : t("common.statusPill.stopped")} />
      <p className="min-w-0 flex-1 basis-64 text-13 text-pretty text-fg">{changing ? t("databases.stopped.changing", { engine }) : t("databases.stopped.body", { engine })}</p>
      {changing ? null : (
        <GuardedButton permission="apps.operate" variant="primary" icon={<VERB_ICONS.turnOn aria-hidden="true" />} loading={actions.starting} disabled={actions.busy} onClick={() => actions.start(server)}>
          {t("databases.stopped.start")}
        </GuardedButton>
      )}
    </section>
  );
}
