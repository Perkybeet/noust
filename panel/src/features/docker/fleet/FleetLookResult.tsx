import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import { lookResultOf } from "./lookResult";

/**
 * What each server found when the fleet looked for new versions: how many images it looked at and how many have a newer one, with
 * which. A server that did not look (no Docker, or a Noust before 4.3) is not in it: the plan said so before, and it is not a failure.
 */
export function FleetLookResult({ result }: { result: unknown }) {
  const t = useT();
  const servers = lookResultOf(result);
  if (servers.length === 0) return null;
  return (
    <ul aria-label={t("docker.versions.fleet.resultLabel")} className="m-0 flex list-none flex-col gap-2 p-0 text-13 text-fg">
      {servers.map((row) => (
        <li key={row.server} className="flex min-w-0 flex-col gap-0.5">
          <span className="text-pretty">{row.newer === 0 ? t("docker.versions.fleet.foundNone", { server: row.server, count: row.looked }) : t("docker.versions.fleet.found", { server: row.server, count: row.looked, newer: row.newer })}</span>
          {row.references.length > 0 ? (
            <span className="flex min-w-0 flex-wrap gap-x-3 text-fg-muted">
              {row.references.map((reference) => (
                <Mono key={reference} truncate>
                  {reference}
                </Mono>
              ))}
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
