/**
 * The check of a unit file, in one place: the request (`POST /api/services/verify`, which is `systemd-analyze verify`
 * on a copy that is never installed) and the way its answer is said. The unit file page asks it before it saves, and
 * the advanced form of a new service asks it before it creates, so a unit is judged the same wherever it is written
 * and systemd's own words are shown the same way (the system's output is never paraphrased).
 */

import { useMutation } from "@tanstack/react-query";

import { request } from "../../api/client";
import { ErrorBlock } from "../../components/page/QueryState";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";

/**
 * Asks systemd whether a candidate unit file is acceptable. Silent: whoever asks draws the answer
 * (`UnitCheckVerdict`), and a save or a creation that goes on to succeed says so itself.
 */
export function useVerifyUnit() {
  return useMutation({
    mutationFn: (content: string) => request("post", "/api/services/verify", { body: { content } }),
  });
}

/** What the check said: refused, with systemd's words, or passed, with whatever it printed. */
export function UnitCheckVerdict({ success, output }: { success: boolean; output: string }) {
  const t = useT();
  if (!success) return <ErrorBlock live compact error={{ detail: output }} title={t("services.unitFile.rejected")} />;
  return (
    <div className="flex flex-col gap-2">
      <InlineMessage tone="success" live>
        {t("services.unitFile.passed")}
      </InlineMessage>
      {output.trim() !== "" ? (
        <SystemOutput label={t("services.unitFile.analyzeOutput")} maxHeight="max-h-32">
          {output}
        </SystemOutput>
      ) : null}
    </div>
  );
}
