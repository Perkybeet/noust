import { Link } from "@tanstack/react-router";

import { useCan } from "../../../components/page/permissions";
import { buttonClassName } from "../../../components/ui/Button";
import { EmptyState } from "../../../components/ui/EmptyState";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import { useScopeOffers } from "../../../nodes/useNode";

/**
 * What the files of an application that runs in containers say when their address is typed or kept: they are the images' and the volumes'
 * and not a folder, so there is nothing to list and the server would refuse the listing. It says where they are and leads to the
 * application's containers when the machine has the Docker zone; it is not an error and has no "Try again" (item 189).
 */
export function FilesNotHere({ domain }: { domain: string }) {
  const t = useT();
  const mayRead = useCan("docker.read");
  const hasZone = useScopeOffers()("docker_zone");
  return (
    <EmptyState
      variant="firstUse"
      icon={<OBJECT_ICONS.container />}
      title={t("runtime.files.containersTitle")}
      description={t("runtime.files.containersBody", { domain })}
      {...(mayRead && hasZone
        ? {
            action: (
              <Link to="/server/docker/containers" search={{ app: domain }} className={buttonClassName("primary", "md")}>
                {t("runtime.files.seeContainers")}
              </Link>
            ),
          }
        : {})}
    />
  );
}
