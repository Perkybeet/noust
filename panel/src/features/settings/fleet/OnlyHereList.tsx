import { Link } from "@tanstack/react-router";

import { buttonClassName } from "../../../components/ui/Button";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import { useOnlyHere } from "./useOnlyHere";
import type { NamedKind } from "./onlyHere";

/**
 * Under what the central carries of a kind: what a server keeps for itself and the fleet does not know, one row for each
 * ("Only web-01 has it. The fleet does not know it."), with the way into that server's page, where its decision about the
 * fleet is made. It is read from the server through the central's proxy and never made a setting of the fleet by being
 * read; the server's own console is where it is changed. Nothing when no server has any.
 */
export function OnlyHereList({ resource }: { resource: NamedKind }) {
  const t = useT();
  const { rows } = useOnlyHere([resource]);
  if (rows.length === 0) return null;
  return (
    <div data-slot="only-here" className="flex flex-col border-t border-border">
      <div className="flex flex-col gap-0.5 px-5 pt-4">
        <p className="text-13 font-medium text-fg">{t("fleetSettings.only.title")}</p>
        <p className="text-13 text-pretty text-fg-muted">{t("fleetSettings.only.description")}</p>
      </div>
      <ul aria-label={t("fleetSettings.only.title")} className="m-0 flex list-none flex-col divide-y divide-border p-0 pt-2">
        {rows.map((row) => (
          <li key={`${row.node}/${row.name}`} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-3">
            <div className="min-w-0 flex-1 basis-56">
              <Mono tone="default" className="text-14 font-medium">
                {row.name}
              </Mono>
              {row.detail !== undefined ? <p className="text-13 break-all text-fg-muted">{row.detail}</p> : null}
              <p className="text-13 text-pretty text-fg-muted">{t.rich("fleetSettings.only.row", { server: <Mono key="server" tone="default">{row.node}</Mono> })}</p>
            </div>
            <Link to="/server/central" search={{ node: row.node }} className={buttonClassName("secondary", "sm")}>
              {t("fleetSettings.panel.openServer", { server: row.node })}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
