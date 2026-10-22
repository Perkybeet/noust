import { useQuery } from "@tanstack/react-query";

import { freezesQuery } from "../../api/queries/rollout";
import { Section } from "../../components/page/Section";
import { useT } from "../../i18n";
import { olderServer } from "../app/settings/queries";
import { useOrganization } from "../teams/useOrganization";
import { FreezeCard } from "./FreezeCard";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/**
 * The freeze of the whole server (`noust deploy freeze`, `POST /api/deploy-freezes`): every deploy
 * on this machine is held for a stretch, apart from rollbacks. An app can also be frozen on its
 * own, from its Deploys settings. A server older than the freezes shows nothing, heading included.
 */
export function ServerFreezeSection() {
  const t = useT();
  const org = useOrganization();
  const query = useQuery({ ...freezesQuery(), retry: false });
  if (query.isError && olderServer(query.error)) return null;
  return (
    <Section title={t("rollout.freeze.serverTitle")} icon={OBJECT_ICONS.freeze} description={t("rollout.freeze.serverDescription")}>
      <FreezeCard canDeploy={org.can("apps.deploy")} title={t("rollout.freeze.title")} />
    </Section>
  );
}
