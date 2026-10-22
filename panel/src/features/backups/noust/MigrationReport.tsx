import { CommandHint } from "../../../components/page/CommandHint";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import type { MigrationReportView } from "./migration";

function stateWord(t: T, state: string): string {
  return state === "done" ? t("selfBackup.migrate.report.copied") : state === "failed" ? t("selfBackup.migrate.report.failed") : t("selfBackup.migrate.report.skipped");
}

/**
 * What a preparation did, once its job ended: each piece with whether it was copied (a piece
 * that failed shows the manager's own words), and the commands to run on the new server in order.
 */
export function MigrationReport({ report }: { report: MigrationReportView }) {
  const t = useT();
  return (
    <Notice
      tone={report.ok ? "success" : "warning"}
      title={report.ok ? t("selfBackup.migrate.report.okTitle", { destination: report.destination }) : t("selfBackup.migrate.report.notOkTitle")}
    >
      <div className="flex flex-col gap-4">
        {!report.ok ? <p>{t("selfBackup.migrate.report.notOkBody")}</p> : null}
        <ul className="flex flex-col gap-1">
          {report.steps.map((step) => (
            <li key={`${step.kind}:${step.name}`} className="flex min-w-0 flex-wrap items-baseline gap-x-2">
              <StatusPill size="sm" state={step.state === "done" ? "running" : step.state === "failed" ? "failed" : "stopped"} label={stateWord(t, step.state)} />
              <Mono tone="default">{step.name}</Mono>
              {step.state === "failed" ? <span className="text-12">{step.detail}</span> : null}
            </li>
          ))}
        </ul>
        <div className="flex flex-col gap-1">
          <p className="font-medium">{t("selfBackup.migrate.report.next")}</p>
          <ol className="flex list-decimal flex-col gap-1 pl-5">
            {report.nextSteps.map((step) => (
              <li key={step}>{step.startsWith("noust ") ? <Mono tone="default">{step}</Mono> : step}</li>
            ))}
          </ol>
        </div>
        <CommandHint command="noust server migrate check" label={t("backups.common.fromTerminal")} />
      </div>
    </Notice>
  );
}
