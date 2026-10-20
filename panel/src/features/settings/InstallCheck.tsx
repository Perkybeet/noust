import { useQuery } from "@tanstack/react-query";
import { CircleMinus, CircleX, RotateCw } from "lucide-react";

import { installCheckQuery } from "../../api/queries/system";
import type { InstallCheck as InstallCheckResult } from "../../api/queries/system";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Subsection } from "../../components/page/Subsection";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Disclosure } from "../../components/ui/Disclosure";
import { ICONS } from "../../components/ui/icons";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";

type Item = InstallCheckResult["sections"][number]["items"][number];

/** An item's outcome as a shape and a word: what passed, what is optional, what to look at, what is missing. */
function Outcome({ status }: { status: Item["status"] }) {
  const t = useT();
  const Icon = status === "ok" ? ICONS.success : status === "warn" ? ICONS.warning : status === "fail" ? CircleX : CircleMinus;
  const colour = status === "ok" ? "text-ok" : status === "warn" ? "text-warn" : status === "fail" ? "text-fail" : "text-idle";
  return (
    <span className="inline-flex h-5 w-24 shrink-0 items-center gap-1.5 text-12 font-medium text-fg-muted">
      <Icon aria-hidden="true" className={cx("size-icon-md shrink-0", colour)} />
      <span className={status === "fail" ? "text-fail" : undefined}>{t(`settings.about.installCheck.status.${status}`)}</span>
    </span>
  );
}

function Row({ item }: { item: Item }) {
  const t = useT();
  return (
    <li className="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-1.5 px-4 py-2.5">
      <Outcome status={item.status} />
      <div className="flex min-w-0 flex-1 basis-64 flex-col gap-1.5">
        {/* The doctor's own words, kept as it wrote them. */}
        <span translate="no" className="text-13 leading-5 text-pretty break-words text-fg">
          {item.text.trim()}
        </span>
        {(item.notes ?? []).map((note) => (
          <span key={note} translate="no" className="text-12 text-fg-muted">
            {note}
          </span>
        ))}
        {item.fix ? <CommandHint label={t("settings.about.installCheck.fix")} command={item.fix} /> : null}
      </div>
    </li>
  );
}

function Verdict({ report }: { report: InstallCheckResult }) {
  const t = useT();
  const Icon = report.ready ? (report.warnings > 0 ? ICONS.warning : ICONS.success) : CircleX;
  const colour = report.ready ? (report.warnings > 0 ? "text-warn" : "text-ok") : "text-fail";
  const words = report.ready
    ? report.warnings > 0
      ? t("settings.about.installCheck.readyWithWarnings", { count: report.warnings })
      : t("settings.about.installCheck.ready")
    : t("settings.about.installCheck.notReady", { count: report.issues });
  return (
    <p role="status" className="flex min-h-5 items-center gap-2 text-14 font-medium text-fg">
      <Icon aria-hidden="true" className={cx("size-icon-md shrink-0", colour)} />
      {words}
    </p>
  );
}

/** The checks themselves, by section, and the command that runs them from a terminal. */
function Details({ report }: { report: InstallCheckResult }) {
  const t = useT();
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <Card padding="none">
        <div className="flex min-w-0 flex-col divide-y divide-border">
          {report.sections.map((section) => (
            <Subsection key={section.title} title={section.title} description={section.explain ?? undefined} className="gap-1 pt-3 pb-1">
              <ul aria-label={t("settings.about.installCheck.listLabel", { section: section.title })} className="flex min-w-0 flex-col">
                {section.items.map((item, index) => (
                  <Row key={`${item.text}-${String(index)}`} item={item} />
                ))}
              </ul>
            </Subsection>
          ))}
        </div>
      </Card>
      <CommandHint label={t("settings.about.installCheck.fromTerminal")} command="noust setup doctor" />
    </div>
  );
}

/**
 * Settings > About > Installation check: `noust setup doctor` on this server, as the console shows
 * it, so what a deployment needs and does not find is read where the operator already is, with the
 * command that fixes each gap. The GitHub connection test is left to the terminal.
 */
export function InstallCheck() {
  const t = useT();
  const check = useQuery(installCheckQuery());
  return (
    <Section
      level={3}
      title={t("settings.about.installCheck.title")}
      loading={check.data === undefined && !check.isError}
      actions={
        <Button size="sm" icon={<RotateCw aria-hidden="true" />} loading={check.isFetching} onClick={() => void check.refetch()}>
          {t("settings.about.installCheck.recheck")}
        </Button>
      }
    >
      {check.data === undefined ? (
        check.isError ? (
          <ErrorBlock compact error={check.error} title={t("settings.about.installCheck.loadFailed")} />
        ) : (
          // The verdict's own height, so what replaces it does not move the page.
          <div aria-hidden="true" className="flex min-h-5 items-center">
            <Skeleton className="h-4 w-48" />
          </div>
        )
      ) : (
        <div className="flex min-w-0 flex-col gap-3">
          <Verdict report={check.data} />
          <Disclosure label={t("settings.about.installCheck.show")}>
            <Details report={check.data} />
          </Disclosure>
        </div>
      )}
    </Section>
  );
}
