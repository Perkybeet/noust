import { Download, ExternalLink } from "lucide-react";
import { useEffect, useRef } from "react";

import { announce } from "../../app/Announcer";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
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
  // What changed on the screen is not known to a person who cannot see it: the end is said once, when it arrives.
  const said = useRef(false);
  useEffect(() => {
    if (said.current) return;
    said.current = true;
    announce(t("report.done.downloaded", { name: fileName }), "polite");
  }, [t, fileName]);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Card
        as="section"
        level={3}
        padding="sm"
        title={t.rich("report.done.downloaded", { name: <Mono key="name">{fileName}</Mono> })}
        actions={
          <Button size="sm" icon={<Download />} onClick={onDownloadAgain}>
            {t("report.done.again")}
          </Button>
        }
      >
        <p className="text-13 text-fg-muted">{t("report.done.where", { size: formatBytes(size, t.locale) })}</p>
      </Card>
      <Card
        as="section"
        level={3}
        padding="sm"
        title={t("report.done.opened")}
        actions={
          <Button size="sm" icon={<ExternalLink />} onClick={onOpenAgain}>
            {t("report.done.openAgain")}
          </Button>
        }
      >
        <ol className="list-decimal space-y-0.5 pl-5 text-13 text-fg-muted">
          <li>{t("report.done.drag", { label: GITHUB_FILE_BOX })}</li>
          <li>{t("report.done.wait")}</li>
          <li>{t("report.done.press", { submit: GITHUB_SUBMIT })}</li>
        </ol>
      </Card>
      {blocked ? <Notice tone="warning">{t("report.done.blocked", { action: t("report.done.openAgain") })}</Notice> : null}
    </div>
  );
}
