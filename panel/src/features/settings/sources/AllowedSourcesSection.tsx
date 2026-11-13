import { useQuery } from "@tanstack/react-query";

import { configQuery, patchConfig } from "../../../api/queries/config";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import { useState } from "react";
import { useRefreshConfig } from "../SettingsForm";
import { readAllowedSources, recipesAllowed, withRecipes } from "./allowed";

/**
 * Where code may come from, on a server that names it (`security.allowed_sources`). Only such a server draws
 * this: with no list every origin is allowed and there is nothing to say. When the list does not allow the
 * catalog's recipes, Noust would refuse the folder it prepares a recipe in, so the console offers the one change
 * that fixes it, with what it does said first, and writes the list whole with the word added.
 */
export function AllowedSourcesSection() {
  const t = useT();
  const refresh = useRefreshConfig();
  const config = useQuery(configQuery());
  const [asking, setAsking] = useState(false);
  const entries = readAllowedSources(config.data?.config);
  if (entries.length === 0) return null;
  const allowed = recipesAllowed(entries);
  return (
    <SurfaceSection title={t("connections.allowed.title")} icon={OBJECT_ICONS.source} description={t("connections.allowed.description")}>
      <ul aria-label={t("connections.allowed.listLabel")} className="m-0 flex list-none flex-wrap gap-2 p-0">
        {entries.map((entry) => (
          <li key={entry}>
            <Mono tone="default">{entry}</Mono>
          </li>
        ))}
      </ul>
      {allowed ? (
        <InlineMessage tone="info">{t("connections.allowed.recipesAllowed")}</InlineMessage>
      ) : (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <InlineMessage tone="warning">{t("connections.allowed.recipesBlocked")}</InlineMessage>
          <Button size="sm" onClick={() => setAsking(true)}>
            {t("connections.allowed.allowRecipes")}
          </Button>
        </div>
      )}
      {asking ? (
        <ConfirmDialog
          friction="simple"
        destructive={false}
          open
          onOpenChange={(next) => {
            if (!next) setAsking(false);
          }}
          title={t("connections.allowed.confirmTitle")}
          description={t("connections.allowed.confirmBody")}
          actionLabel={t("connections.allowed.allowRecipes")}
          onConfirm={async () => {
            await patchConfig("security.allowed_sources", withRecipes(entries));
            await refresh();
          }}
        />
      ) : null}
    </SurfaceSection>
  );
}
