import { Link } from "@tanstack/react-router";

import { HelpPopover } from "../../../components/ui/HelpPopover";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { StatusServer, StatusSetting } from "./data";
import { conditionsOf, followWords, marksOf } from "./faces";
import type { FollowWords, Mark } from "./faces";
import { FaceMark } from "./FaceMark";
import { useFleetServers } from "./useFleetKind";

/** The line, said. */
function sentence(t: ReturnType<typeof useT>, words: FollowWords): string {
  switch (words.kind) {
    case "single":
      return t("fleetSettings.follow.single");
    case "all":
      return t("fleetSettings.follow.all", { of: words.of });
    case "some":
      return t("fleetSettings.follow.some", { following: words.following, of: words.of });
    case "one":
      return t("fleetSettings.follow.one", { of: words.of });
    case "none":
      return t("fleetSettings.follow.none");
  }
}

/** What a mark says on a row in a few words: the server is named before it, in mono. */
function Mark({ mark, onOpen }: { mark: Mark; onOpen: (node: string) => void }) {
  const t = useT();
  const word = t(`fleetSettings.mark.${mark.face === "exception" || mark.face === "changedHere" || mark.face === "pending" ? mark.face : "off"}`);
  return (
    <button
      type="button"
      onClick={() => onOpen(mark.node)}
      className="-mx-1 inline-flex min-h-6 cursor-pointer items-center gap-1.5 rounded-control px-1 text-12 hover:bg-surface-hover focus-visible:-outline-offset-2"
    >
      <Mono tone="default">{mark.node}</Mono>
      {mark.face === "exception" ? (
        <span className="text-fg-muted">{word}</span>
      ) : (
        <StatusPill state={mark.face === "changedHere" ? "warning" : "queued"} label={word} appearance="inline" size="sm" />
      )}
    </button>
  );
}

/**
 * Under a setting: "2 of 4 servers follow it", and beside it only what is true of this setting on a server (it has a
 * value of its own, somebody changed it there, a change has not arrived). The count is the server's; where it does not
 * send one (a central of 4.2) the line is not drawn, and the marks still are. A press opens the panel of the setting.
 */
export function FollowLine({ row, onOpen }: { row: StatusSetting; onOpen: (node?: string) => void }) {
  const t = useT();
  const marks = marksOf(row);
  const counted = row.following !== undefined && row.following !== null && row.of !== undefined && row.of !== null;
  if (!counted && marks.length === 0) return null;
  const words = counted ? followWords(row.following ?? 0, row.of ?? 0) : null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {words !== null ? (
        <button
          type="button"
          onClick={() => onOpen()}
          className="-mx-1 cursor-pointer rounded-control px-1 text-left text-13 text-fg-muted hover:bg-surface-hover hover:text-fg focus-visible:-outline-offset-2"
        >
          {sentence(t, words)}
        </button>
      ) : null}
      {marks.map((mark) => (
        <Mark key={mark.node} mark={mark} onOpen={onOpen} />
      ))}
    </div>
  );
}

/** The strip of a page of the settings, which asks the servers itself: nothing on a lone server and for a person who may not read the fleet. */
export function FleetStrip() {
  const { active, servers } = useFleetServers();
  return active ? <NodeStrip servers={servers} /> : null;
}

/**
 * Once under the page's title: what stands in the way of a server whatever the setting. Each is a link to the page
 * of that server, because independence and the ceiling are decided there and not in a setting. Nothing when no server
 * is in the way, and nothing at all on a lone server.
 */
export function NodeStrip({ servers }: { servers: readonly StatusServer[] }) {
  const t = useT();
  const conditions = conditionsOf(servers);
  if (conditions.length === 0) return null;
  return (
    <div className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-2" data-slot="node-strip">
      <span className="flex items-center gap-1 text-13 text-fg-muted">
        {t("fleetSettings.strip.title")}
        <HelpPopover label={t("fleetSettings.strip.help")} title={t("fleetSettings.strip.helpTitle")}>
          <ul className="m-0 flex list-none flex-col gap-2 p-0 text-13 text-fg-muted">
            <li>{t("fleetSettings.strip.off")}</li>
            <li>{t("fleetSettings.strip.independent")}</li>
            <li>{t("fleetSettings.strip.refused")}</li>
            <li>{t("fleetSettings.strip.unsupported")}</li>
          </ul>
        </HelpPopover>
      </span>
      {conditions.map((condition) => (
        <Link
          key={condition.node}
          to="/server/central"
          search={{ node: condition.node }}
          aria-label={t("fleetSettings.strip.open", { server: condition.node })}
          className="-mx-1 inline-flex min-h-7 items-center gap-1.5 rounded-control px-1 text-13 hover:bg-surface-hover focus-visible:-outline-offset-2"
        >
          <Mono tone="default">{condition.node}</Mono>
          <FaceMark face={condition.face} />
        </Link>
      ))}
    </div>
  );
}
