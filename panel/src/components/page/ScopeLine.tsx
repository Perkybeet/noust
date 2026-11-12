import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Mono } from "../ui/Mono";
import { OBJECT_ICONS } from "../ui/objectIcons";
import { StatusGlyph } from "../ui/StatusPill";
import { TextLink } from "../ui/TextLink";
import { ANSWER_STATE, answerOf, isFresh, useSilence } from "./fleetServers";
import type { FleetServer, ServerTarget } from "./fleetServers";

/**
 * Whose a page is. `all`: the servers that have what the page lists (each with how it answered); `server`: the one the
 * selector is on; `library`: what is the same on every server and has nobody to answer for it (the catalog's recipes).
 */
export type ScopeLineScope =
  | { kind: "all"; servers: readonly FleetServer[] }
  | { kind: "server"; server: FleetServer }
  | { kind: "library" };

export interface ScopeLineProps {
  scope: ScopeLineScope;
  /**
   * Where a server it names opens (its own page, to see why it does not answer). Without it the name is only a name.
   * The kit never decides what choosing a server does to the selector: that is the page's.
   */
  serverTarget?: (server: FleetServer) => ServerTarget;
  /** The way to see them all, after "From web-01". On by default; off where the page says it elsewhere. */
  hint?: boolean;
  className?: string;
}

/** A server's name in mono, opening its page when the page says where. */
function ServerMention({ server, target }: { server: FleetServer; target: ServerTarget | undefined }) {
  const name = <Mono tone="default">{server.name}</Mono>;
  if (target === undefined) return name;
  return (
    <TextLink to={target.to} search={target.search as never} variant="row">
      {name}
    </TextLink>
  );
}

/** One server that did not answer fresh and whole: its state's glyph and a sentence that says what is shown of it. */
function Troubled({ server, target }: { server: FleetServer; target: ServerTarget | undefined }) {
  const t = useT();
  const answer = answerOf(server);
  const age = useSilence(answer.kind === "stale" ? answer.since : null);
  if (answer.kind === "fresh") return null;
  const mention = <ServerMention key="server" server={server} target={target} />;
  const sentence =
    answer.kind === "stale"
      ? age === null
        ? t.rich("fleetCompare.scope.staleNoAge", { server: mention })
        : t.rich("fleetCompare.scope.stale", { server: mention, age })
      : answer.kind === "unreachable"
        ? t.rich("fleetCompare.scope.unreachable", { server: mention })
        : answer.version === undefined || answer.version === null || answer.version === ""
          ? t.rich("fleetCompare.scope.unsupportedNoVersion", { server: mention })
          : t.rich("fleetCompare.scope.unsupported", { server: mention, version: answer.version });
  return (
    // The glyph stays by the first line when the sentence wraps on a phone, not between the lines.
    <li className="flex items-start gap-2 text-13 text-fg-muted">
      <StatusGlyph state={ANSWER_STATE[answer.kind]} size={12} className="mt-1" />
      <span className="min-w-0">{sentence}</span>
    </li>
  );
}

/**
 * What a page says of whose it is, once and in the same place on every page: the first line of the body, under the
 * title (never in the header, which does not repeat the server, 6.11), and after it, one line for each server that
 * did not answer fresh and whole or whose Noust does not know the view. With All servers it counts the servers it
 * speaks of; with one it names it; a lone server has no use for it and the page draws none. Colour is only the
 * glyph of a state: the sentences stay in the stone's text, so a page of servers that all answered is calm.
 */
export function ScopeLine({ scope, serverTarget, hint = true, className }: ScopeLineProps) {
  const t = useT();
  const servers = scope.kind === "all" ? scope.servers : scope.kind === "server" ? [scope.server] : [];
  const troubled = servers.filter((server) => !isFresh(server));
  // The rows shown are the fresh ones and the last that a stale one gave; the other two kinds have none here.
  const shown = servers.filter((server) => {
    const kind = answerOf(server).kind;
    return kind === "fresh" || kind === "stale";
  }).length;
  const Icon = scope.kind === "server" ? OBJECT_ICONS.server : OBJECT_ICONS.node;
  const target = (server: FleetServer): ServerTarget | undefined => serverTarget?.(server);

  return (
    <div data-scope-line="" className={cx("flex min-w-0 flex-col gap-1.5", className)}>
      <p className="flex items-start gap-2 text-13 text-fg-muted">
        <Icon aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0 text-fg-faint" />
        <span className="min-w-0">
          {scope.kind === "library"
            ? t("fleetCompare.scope.library")
            : scope.kind === "server"
              ? t.rich("fleetCompare.scope.server", { server: <Mono key="server" tone="default">{scope.server.name}</Mono> })
              : shown === 0
                ? t("fleetCompare.scope.none")
                : t("fleetCompare.scope.all", { count: shown })}
          {scope.kind === "server" && hint ? ` ${t("fleetCompare.scope.serverHint")}` : ""}
        </span>
      </p>
      {troubled.length > 0 ? (
        <ul aria-label={t("fleetCompare.scope.troubleLabel")} className="flex flex-col gap-1.5">
          {troubled.map((server) => (
            <Troubled key={server.name} server={server} target={target(server)} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}
