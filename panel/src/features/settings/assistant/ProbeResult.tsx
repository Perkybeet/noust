import type { AssistantProbe } from "../../../api/queries/assistant";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { RowList } from "../../../components/ui/RowList";
import { ServerText } from "../../../components/ui/ServerText";
import { StatusGlyph } from "../../../components/ui/StatusPill";
import { Subsection } from "../../../components/page/Subsection";
import { useT } from "../../../i18n";
import { probeState } from "./model";

const WORDS = { running: "assistantSettings.model.probe.ok", warning: "assistantSettings.model.probe.warning", failed: "assistantSettings.model.probe.failed" } as const;

/**
 * What the probe found, in the sentences Noust wrote of it (the provider answers; the model exists; the model made the test call
 * well; for a model on this machine, an honest word about memory), each with the glyph and the word of its state and never a
 * colour alone. Then one line that says what the assistant will be able to do with this model: a model that did not make the call
 * only explains, and nothing else is offered.
 */
export function ProbeResult({ probe }: { probe: AssistantProbe }) {
  const t = useT();
  return (
    <Subsection title={t("assistantSettings.model.result")}>
      <RowList aria-label={t("assistantSettings.model.result")}>
        {(probe.sentences ?? []).map((sentence, index) => {
          const state = probeState(sentence.code);
          return (
            <RowList.Item key={`${sentence.code}:${String(index)}`} leading={<StatusGlyph state={state} />}>
              <div className="min-w-0 py-1">
                <p className="text-13 font-medium text-fg">
                  <span className="sr-only">{t(WORDS[state])}. </span>
                  <ServerText coded={sentence} text={sentence.text} />
                </p>
                {sentence.hint !== null && sentence.hint !== undefined ? (
                  <p className="mt-0.5 text-13 text-pretty text-fg-muted">
                    <ServerText coded={sentence} text={sentence.hint} part="hint" />
                  </p>
                ) : null}
              </div>
            </RowList.Item>
          );
        })}
      </RowList>
      {probe.reachable && probe.model_found !== false ? <InlineMessage tone="info">{probe.tools === true ? t("assistantSettings.model.probe.toolsYes") : t("assistantSettings.model.probe.toolsNo")}</InlineMessage> : null}
    </Subsection>
  );
}
