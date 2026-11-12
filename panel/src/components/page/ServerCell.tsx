import { useT } from "../../i18n";
import { Mono } from "../ui/Mono";
import { StatusGlyph } from "../ui/StatusPill";
import { answerOf, useSilence } from "./fleetServers";
import type { FleetServer } from "./fleetServers";

/** A server's name in mono, with "central" after it when it is this central: one more server of the list, marked. */
export function ServerName({ server }: { server: FleetServer }) {
  const t = useT();
  return (
    <span className="flex min-w-0 items-center gap-2">
      <Mono tone="default" truncate>
        {server.name}
      </Mono>
      {server.central === true ? <span className="shrink-0 text-12 text-fg-muted">{t("fleetCompare.names.central")}</span> : null}
    </span>
  );
}

/**
 * The server a row came from, for the column of a list and of a table of servers: its name, and under it, when it
 * did not answer now, that what the row says is the last it gave and how old that is. The mark is the glyph of a
 * warning and words (a state is never a colour alone); the colour of the words stays the stone's.
 */
export function ServerCell({ server }: { server: FleetServer }) {
  const t = useT();
  const answer = answerOf(server);
  const age = useSilence(answer.kind === "stale" ? answer.since : null);
  return (
    <span className="flex min-w-0 flex-col items-start gap-0.5">
      <ServerName server={server} />
      {answer.kind === "stale" ? (
        <span className="inline-flex items-center gap-1 text-12 text-fg-muted">
          <StatusGlyph state="warning" size={10} />
          {age === null ? t("fleetCompare.stale.lastGiven") : t("fleetCompare.stale.dataFrom", { age })}
        </span>
      ) : null}
    </span>
  );
}
