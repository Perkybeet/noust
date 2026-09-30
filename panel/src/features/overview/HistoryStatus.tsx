import type { MetricsCollector, MetricsRead } from "../../api/queries/metrics";
import { CommandHint } from "../../components/page/CommandHint";
import { truthLine } from "../../components/ui/Chart";
import { resolutionWords } from "../../components/ui/chart/time";
import { Notice } from "../../components/ui/Notice";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { PlainKey, T } from "../../i18n";
import { formatDateTime } from "../../lib/format";
import { rangeSpec } from "./ranges";
import type { MetricRange } from "./ranges";

/**
 * What the charts below show, said from the answer rather than from the button pressed: the
 * window, what each point is and since when there is history. The direct answer to "the range
 * selector changes nothing": even with no data the sentence and the axis say what was asked.
 * Said again politely when the range changes.
 */
export function TruthLine({ read, range, reading }: { read: MetricsRead | undefined; range: MetricRange; reading: boolean }) {
  const t = useT();
  let text: string;
  if (read === undefined || reading) text = t("overview.machine.loadingRange", { range: t(rangeSpec(range).words).toLowerCase() });
  else text = truthLine(t, [read.from, read.to], resolutionWords(t, read.resolution, read.step), read.first_sample_at ?? null);
  return (
    <span aria-live="polite" data-truth="">
      {text}
    </span>
  );
}

const COLLECTOR_REASONS: Readonly<Record<string, PlainKey>> = {
  collector_stopped: "overview.history.reason.collector_stopped",
  monitor_not_installed: "overview.history.reason.monitor_not_installed",
  monitor_disabled: "overview.history.reason.monitor_disabled",
  monitor_not_running: "overview.history.reason.monitor_not_running",
  collector_stalled: "overview.history.reason.collector_stalled",
};

/** Why history is not being recorded, in the console's words; Noust's own sentence for a code it does not know. */
export function collectorReasonText(t: T, reason: { code: string; message: string }): string {
  const key = COLLECTOR_REASONS[reason.code];
  return key ? t(key) : reason.message;
}

/**
 * Says when the machine's history is not being recorded, why, and the command that fixes it;
 * or, quietly, that it is recorded only while the console runs. Nothing when all is well.
 */
export function CollectorNotice({ collector }: { collector: MetricsCollector | undefined }) {
  const t = useT();
  if (collector === undefined) return null;
  if (!collector.recording) {
    const reason = collector.reason;
    const last = collector.last_sample_at ?? null;
    return (
      <Notice tone="warning" title={t("overview.history.notRecordingTitle")}>
        <div className="flex flex-col gap-2">
          <p>
            {reason ? collectorReasonText(t, reason) : null}
            {last !== null ? ` ${t("overview.history.lastReading", { time: formatDateTime(new Date(last * 1000), t.locale) })}` : ""}
          </p>
          {reason?.fix ? <CommandHint label={t("overview.history.fixLead")} command={reason.fix} /> : null}
          {reason?.evidence ? <SystemOutput label={t("appPages.metrics.evidenceLabel")}>{reason.evidence}</SystemOutput> : null}
        </div>
      </Notice>
    );
  }
  const advice = collector.advice;
  if (advice?.code !== "console_only") return null;
  return (
    <Notice title={t("overview.history.consoleOnlyTitle")}>
      <div className="flex flex-col items-start gap-2">
        <p>{t("overview.history.consoleOnly")}</p>
        {advice.fix ? <CommandHint command={advice.fix} /> : null}
      </div>
    </Notice>
  );
}
