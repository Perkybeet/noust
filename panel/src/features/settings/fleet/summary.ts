/**
 * What a setting of the fleet holds, in one line for a row. The value is the body of the server's own route for it
 * (see `fleet/settings/kinds.py`), so each kind says its own: the type and where it goes, the comparison a rule makes,
 * where copies are kept, the account of a registry. A credential is never part of it: a secret arrives as `***` and
 * stays so, and a URL is shown as its host because a webhook's address carries its token.
 */

type Value = Record<string, unknown>;

const MAX = 80;

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : typeof value === "number" ? String(value) : "";
}

function record(value: unknown): Value {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Value) : {};
}

function host(address: string): string {
  try {
    return new URL(address).hostname;
  } catch {
    return "";
  }
}

function join(...parts: readonly string[]): string {
  return parts.filter((part) => part !== "").join(", ");
}

function cut(value: string): string {
  return value.length > MAX ? `${value.slice(0, MAX)}…` : value;
}

/** Where a channel goes, from the settings that name a place and are not a credential. */
function channelPlace(settings: Value): string {
  for (const key of ["channel", "chat_id", "to", "recipient", "host"]) {
    const found = text(settings[key]);
    if (found !== "" && found !== "***") return found;
  }
  const url = text(settings["url"]);
  return url === "" || url === "***" ? "" : host(url);
}

function minutes(seconds: unknown): string {
  const n = typeof seconds === "number" ? seconds : Number.NaN;
  return Number.isFinite(n) && n > 0 ? `${String(Math.round(n / 60))} min` : "";
}

/** The line a row says for a setting of this kind; empty for a kind this console does not know. */
export function summarize(resource: string, value: Value): string {
  switch (resource) {
    case "channel":
      return cut(join(text(value["type"]), channelPlace(record(value["settings"]))));
    case "alert_rule":
      return cut(join(`${text(value["metric"])} ${text(value["comparator"]) || ">"} ${text(value["threshold"])}`.trim(), minutes(value["duration_s"])));
    case "backup_destination": {
      const fields = record(value["fields"]);
      return cut(join(text(value["backend"]), text(fields["bucket"]) || text(fields["path"]) || text(fields["host"])));
    }
    case "registry":
      return cut(join(text(value["server"]) || text(value["kind"]), text(value["username"])));
    case "variable":
      return value["secret"] === true ? "***" : cut(text(value["value"]));
    case "console_link":
      return cut(text(value["url"]));
    default:
      return "";
  }
}
