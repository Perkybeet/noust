import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { StatusPill } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import type { SsoTestResult } from "../../../api/queries/ssoAdmin";
import { useT } from "../../../i18n";

/** What the provider answered to the test, check by check, in its own words. */
export function TestResultDialog({ name, result, onClose }: { name: string; result: SsoTestResult; onClose: () => void }) {
  const t = useT();
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      size="md"
      title={t("ssoAdmin.providers.testResult.title", { name })}
      description={t("ssoAdmin.providers.testResult.notice")}
      footer={<Button onClick={onClose}>{t("common.words.close")}</Button>}
    >
      <div className="flex flex-col gap-4">
        <Notice tone={result.ok ? "success" : "error"} live>
          {result.ok ? t("ssoAdmin.providers.testResult.passed") : t("ssoAdmin.providers.testResult.failed")}
        </Notice>
        <ul className="flex flex-col gap-3">
          {result.checks.map((check) => (
            <li key={check.name} className="flex min-w-0 flex-col gap-1">
              <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <Mono tone="default">{check.name}</Mono>
                <StatusPill
                  state={check.ok ? "running" : "failed"}
                  label={check.ok ? t("ssoAdmin.providers.testResult.checkPassed") : t("common.words.failedFeminine")}
                  appearance="inline"
                  size="sm"
                />
              </span>
              <SystemOutput label={t("ssoAdmin.providers.testResult.checkOutput", { check: check.name })}>{check.detail}</SystemOutput>
            </li>
          ))}
        </ul>
      </div>
    </Dialog>
  );
}
