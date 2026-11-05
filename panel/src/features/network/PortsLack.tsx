import { useQueryClient } from "@tanstack/react-query";

import { networkKeys } from "../../api/queries/network";
import type { StreamList } from "../../api/queries/network";
import { Notice } from "../../components/ui/Notice";
import { ServerText, useServerSentence } from "../../components/ui/ServerText";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import { sentenceKey } from "../../lib/serverSentence";
import { MissingSoftware } from "../server/software/MissingSoftware";

/**
 * Why nginx here cannot publish a port. What is missing, when it is a piece Noust installs, is said where every missing piece is
 * said, with its install; the rest (a loader nginx does not read, a block of the operator's) is the server's own explanation.
 * The published ports say it above their list, and the dialog that opens a port says it when the port needs nginx.
 */
export function PortsLack({ support }: { support: NonNullable<StreamList["support"]> }) {
  const t = useT();
  const say = useServerSentence();
  const queryClient = useQueryClient();
  const explanation = (
    <Notice tone="warning" title={support.problem ? say(support.coded, support.problem) : t("network.ports.unavailable")}>
      {/* A fix that is a sentence of Noust's is said in this language, its command in mono; one that is nginx's own output is shown as it came. */}
      {support.coded && sentenceKey(support.coded, "hint") !== null ? (
        <p className="text-13 text-pretty">
          <ServerText coded={support.coded} text={support.hint} part="hint" />
        </p>
      ) : support.hint ? (
        <SystemOutput label={t("network.ports.unavailable")}>{support.hint}</SystemOutput>
      ) : null}
    </Notice>
  );
  if (support.install == null) return explanation;
  return <MissingSoftware component={support.install} onInstalled={() => void queryClient.invalidateQueries({ queryKey: networkKeys.streams })} fallback={explanation} />;
}
