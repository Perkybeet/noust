import { useMutation } from "@tanstack/react-query";
import { Download, LifeBuoy } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import type { SupportReport as Report } from "../../api/queries/system";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { CopyButton } from "../../components/ui/CopyButton";
import { IconButton } from "../../components/ui/IconButton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import { downloadText } from "../../lib/clipboard";

/**
 * Settings > About > Support report: the text an issue needs, made on demand. The server masks the
 * secrets it knows and whatever looks like one before it answers, and leaves the journal out for a
 * credential that may not reveal secrets; the operator reads it here before it goes anywhere.
 */
export function SupportReport() {
  const t = useT();
  const [hideDomains, setHideDomains] = useState(false);
  const create = useMutation<Report>({
    mutationFn: () => request("get", "/api/system/support-report", { query: { hide_domains: hideDomains } }),
  });
  const report = create.data;
  const journalLeftOut = report?.sections.some((section) => section.key === "journal" && !section.collected) ?? false;
  return (
    <Section
      level={3}
      title={t("settings.about.supportReport.title")}
      description={t("settings.about.supportReport.description")}
      actions={
        <Button size="sm" variant="primary" icon={<LifeBuoy aria-hidden="true" />} loading={create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? t("settings.about.supportReport.creating") : t("settings.about.supportReport.create")}
        </Button>
      }
    >
      <div className="flex min-w-0 flex-col gap-3">
        <Checkbox
          checked={hideDomains}
          onCheckedChange={setHideDomains}
          label={t("settings.about.supportReport.hideDomains")}
          description={t("settings.about.supportReport.hideDomainsHint")}
        />
        {create.isError ? <ErrorBlock compact live error={create.error} title={t("settings.about.supportReport.failed")} /> : null}
        {report !== undefined ? (
          <Card
            title={t("settings.about.supportReport.outputLabel")}
            level={3}
            padding="sm"
            actions={
              <>
                <CopyButton value={report.markdown} label={t("settings.about.supportReport.copy")} />
                <IconButton
                  size="sm"
                  label={t("settings.about.supportReport.download")}
                  icon={<Download />}
                  onClick={() => downloadText("noust-support-report.md", report.markdown, "text/markdown")}
                />
              </>
            }
          >
            <div className="flex min-w-0 flex-col gap-3">
              <p className="text-13 text-fg-muted">{t("settings.about.supportReport.readFirst")}</p>
              {journalLeftOut ? <p className="text-13 text-fg-muted">{t("settings.about.supportReport.journalLeftOut")}</p> : null}
              <SystemOutput label={t("settings.about.supportReport.outputLabel")} maxHeight="max-h-96" className="rounded-control border border-border bg-bg-sunken px-3 py-2 leading-5">
                {report.markdown}
              </SystemOutput>
            </div>
          </Card>
        ) : null}
        <CommandHint label={t("settings.about.supportReport.fromTerminal")} command="noust support report" />
      </div>
    </Section>
  );
}
