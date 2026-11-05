import type { UseQueryResult } from "@tanstack/react-query";

import { ErrorBlock } from "../../components/page/QueryState";
import { Checkbox } from "../../components/ui/Checkbox";
import { Disclosure } from "../../components/ui/Disclosure";
import { Notice } from "../../components/ui/Notice";
import { Spinner } from "../../components/ui/Spinner";
import { isApiError } from "../../api/client";
import { useT } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { MAX_ATTACHMENT_BYTES, DESCRIPTION_FILE, totalSize } from "./bundle";
import type { ReportFile } from "./bundle";
import { HiddenText, HiddenWord } from "./HiddenText";
import { pieces } from "./redact";
import type { SupportReport } from "./queries";

/** What the person is told of one part of the file: how much it weighs and, when something in it was hidden, how many. */
function summaryOf(file: ReportFile, locale: Parameters<typeof formatBytes>[1]): { size: string; hidden: number } {
  const hidden = file.kind === "text" ? pieces(file.text).filter((piece) => piece.hidden).length : 0;
  return { size: formatBytes(file.bytes.length, locale), hidden };
}

/**
 * The second step: what goes in the file. The support report is on by default and made now by the server (already
 * masked); the browser's block and the images follow. Each part opens and shows exactly what is written in it, with
 * what was hidden marked as such, because the report is public and the person decides what leaves.
 */
export function StepSend({
  includeSupport,
  onIncludeSupport,
  hideDomains,
  onHideDomains,
  support,
  files,
  cutName,
}: {
  includeSupport: boolean;
  onIncludeSupport: (include: boolean) => void;
  hideDomains: boolean;
  onHideDomains: (hide: boolean) => void;
  support: UseQueryResult<SupportReport>;
  files: readonly ReportFile[];
  /** The part that holds the whole of the person's text, when the address of the form had to cut it. */
  cutName: string | null;
}) {
  const t = useT();
  const denied = isApiError(support.error) && support.error.status === 403;
  const journalLeftOut = support.data?.sections.some((section) => section.key === "journal" && !section.collected) ?? false;
  const size = totalSize(files);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <p className="text-13 text-fg-muted">{t.rich("report.send.intro", { hidden: <HiddenWord key="hidden" /> })}</p>
      <div className="flex flex-col gap-3">
        <Checkbox
          checked={includeSupport}
          onCheckedChange={onIncludeSupport}
          label={t("report.send.supportLabel")}
          description={t("report.send.supportHelp")}
        />
        {includeSupport ? (
          <Checkbox
            checked={hideDomains}
            onCheckedChange={onHideDomains}
            label={t("report.send.hideDomains")}
            description={t("report.send.hideDomainsHelp")}
          />
        ) : null}
        {includeSupport && support.isPending ? (
          <p role="status" className="flex items-center gap-2 text-13 text-fg-muted">
            <Spinner size={14} />
            {t("report.send.collecting")}
          </p>
        ) : null}
        {includeSupport && support.isError && denied ? <Notice tone="info">{t("report.send.denied")}</Notice> : null}
        {includeSupport && support.isError && !denied ? (
          <ErrorBlock
            compact
            live
            error={support.error}
            title={t("report.send.failed")}
            onRetry={() => void support.refetch()}
            retrying={support.isFetching}
          />
        ) : null}
        {includeSupport && journalLeftOut ? <p className="text-13 text-fg-muted">{t("report.send.journalLeftOut")}</p> : null}
      </div>
      <ul aria-label={t("report.send.parts")} className="flex min-w-0 flex-col gap-2">
        {files.map((file) => {
          const { size: weight, hidden } = summaryOf(file, t.locale);
          return (
            <li key={file.name} className="min-w-0">
              <Disclosure
                variant="card"
                label={file.name}
                summary={hidden > 0 ? t("report.send.sizeHidden", { size: weight, count: hidden }) : t("report.send.size", { size: weight })}
              >
                {file.kind === "text" ? (
                  <HiddenText text={file.text} label={file.name} />
                ) : (
                  <div className="flex flex-col gap-2">
                    <p className="text-12 text-fg-muted">{t("report.send.imageNote")}</p>
                    <img src={file.dataUrl} alt={file.name} className="elev-carved-fine max-h-64 w-auto max-w-full rounded-control bg-bg-sunken object-contain" />
                  </div>
                )}
              </Disclosure>
            </li>
          );
        })}
      </ul>
      {cutName !== null && files.some((file) => file.name === DESCRIPTION_FILE) ? (
        <Notice tone="info">{t("report.send.cutNote", { name: cutName })}</Notice>
      ) : null}
      {size > MAX_ATTACHMENT_BYTES ? (
        <Notice tone="warning" live>
          {t("report.send.tooBig", { size: formatBytes(size, t.locale), max: formatBytes(MAX_ATTACHMENT_BYTES, t.locale) })}
        </Notice>
      ) : null}
    </div>
  );
}
