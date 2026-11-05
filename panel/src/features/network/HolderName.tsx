import { Link } from "@tanstack/react-router";

import { Mono } from "../../components/ui/Mono";
import { textLinkClassName } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { Holder } from "../server/network/data";

/** One word that says what kind of holder it is, then its name: an application's is a link to it. */
function Named({ kind, owner, to }: { kind: string; owner: string; to?: { domain: string } }) {
  return (
    <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
      <span>{kind}</span>
      {to !== undefined ? (
        <Link to="/apps/$domain" params={{ domain: to.domain }} className={textLinkClassName("inline", "min-w-0", "row")}>
          <Mono truncate>{owner}</Mono>
        </Link>
      ) : (
        <Mono truncate>{owner}</Mono>
      )}
    </span>
  );
}

/**
 * What holds a port, said the way a person says it: the web server, an application (a link to it), a database, a
 * published port. The one reading of a holder, in the table of listening ports and in the rules of the firewall.
 */
export function HolderName({ holder }: { holder: Holder }) {
  const t = useT();
  switch (holder.kind) {
    case "web":
      return <span>{t("serverPorts.holder.web")}</span>;
    case "ssh":
      return <span>{t("serverPorts.holder.ssh")}</span>;
    case "console":
      return <span>{t("serverPorts.holder.console")}</span>;
    case "dns":
      return <span>{t("serverPorts.holder.dns")}</span>;
    case "app":
      return <Named kind={t("serverPorts.holder.app")} owner={holder.owner} to={{ domain: holder.owner }} />;
    case "stack":
      return <Named kind={t("serverPorts.holder.stack")} owner={holder.owner} to={{ domain: holder.owner }} />;
    case "stream":
      return <Named kind={t("serverPorts.holder.stream")} owner={holder.owner} />;
    case "database":
      return <Named kind={t("serverPorts.holder.database")} owner={holder.owner} />;
    case "instance":
      return <Named kind={t("serverPorts.holder.instance")} owner={holder.owner} />;
    case "process":
      return (
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
          <span className="text-fg-muted">{t("serverPorts.holder.process")}</span>
          <Mono truncate>{holder.owner}</Mono>
        </span>
      );
  }
}
