import { Download, ExternalLink } from "lucide-react";

import { Button } from "../../components/ui/Button";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { formatBytes } from "../../lib/format";

/** The words of GitHub's own form the instructions quote: they are what the person will read there. */
export const GITHUB_SUBMIT = "Submit new issue";
export const GITHUB_FILE_BOX = "The report file from the console";

/**
 * After the button: the file that was downloaded, GitHub opened with the form filled in, and the three things
 * left for the person to do there. Both can be done again from here, because a browser may have refused the new
 * tab and a download may have gone somewhere the person did not look.
 */
export function StepDone({
  fileName,
  size,
  blocked,
  onDownloadAgain,
  onOpenAgain,
}: {
  fileName: string;
  size: number;
  /** The browser did not open the tab GitHub's form was meant for. */
  blocked: boolean;
  onDownloadAgain: () => void;
  onOpenAgain: () => void;
}) {
  const t = useT();
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Notice
        tone="success"
        live
        title={t.rich("report.done.downloaded", { name: <Mono key="name">{fileName}</Mono> })}
        action={
          <Button size="sm" icon={<Download />} onClick={onDownloadAgain}>
            {t("report.done.again")}
          </Button>
        }
      >
        {t("report.done.where", { size: formatBytes(size, t.locale) })}
      </Notice>
      <Notice
        tone={blocked ? "warning" : "info"}
        title={t("report.done.opened")}
        action={
          <Button size="sm" icon={<ExternalLink />} onClick={onOpenAgain}>
            {t("report.done.openAgain")}
          </Button>
        }
      >
        <ol className="mt-1 list-decimal space-y-0.5 pl-5">
          <li>{t("report.done.drag", { label: GITHUB_FILE_BOX })}</li>
          <li>{t("report.done.wait")}</li>
          <li>{t("report.done.press", { submit: GITHUB_SUBMIT })}</li>
        </ol>
        {blocked ? <p className="mt-2">{t("report.done.blocked", { action: t("report.done.openAgain") })}</p> : null}
      </Notice>
    </div>
  );
}
