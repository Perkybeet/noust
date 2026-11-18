/**
 * What a stack that already runs gives this server to its containers, said before it is adopted or brought to Noust: one line of its
 * Compose file for each thing (a device, the Docker socket, a path of the server), the sentence the server says of them, and the
 * decision. Accepting is equivalent to root on this server, so it is a box of its own with the whole sentence beside it and not one
 * more button, and only an account that holds the root-equivalent permission is offered it: anybody else reads the lines and who can
 * accept them, and nothing to tick. The lines are the server's own words and are shown as they came.
 */

import { ServerText } from "../../components/ui/ServerText";
import { Checkbox } from "../../components/ui/Checkbox";
import { Notice } from "../../components/ui/Notice";
import { useCan } from "../../components/page/permissions";
import { useT } from "../../i18n";
import type { HostAccess } from "../../lib/hostAccess";

export interface HostAccessLinesProps {
  /** What the refusal listed. */
  access: HostAccess;
  /** Whether the person has said yes: the box. */
  accepted: boolean;
  onAcceptedChange: (accepted: boolean) => void;
}

export function HostAccessLines({ access, accepted, onAcceptedChange }: HostAccessLinesProps) {
  const t = useT();
  const mayAccept = useCan("root_equivalent");
  return (
    <div data-slot="host-access" className="flex min-w-0 flex-col gap-3">
      <Notice tone="warning" title={<ServerText coded={access.coded} text={access.detail} />}>
        <ul aria-label={t("docker.hostAccess.linesLabel")} className="m-0 flex list-disc flex-col gap-1 pl-5 text-13">
          {access.lines.map((line) => (
            <li key={line} className="break-words" translate="no">
              {line}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-13 text-pretty">{t("docker.hostAccess.why")}</p>
      </Notice>
      {mayAccept ? (
        <Checkbox label={t("docker.hostAccess.accept", { count: access.lines.length })} description={t("docker.hostAccess.acceptHelp")} checked={accepted} onCheckedChange={onAcceptedChange} />
      ) : (
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("docker.hostAccess.who")}</p>
      )}
    </div>
  );
}
