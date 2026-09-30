import { Link } from "@tanstack/react-router";

import type { Engine } from "../../api/queries/databases";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { ICONS } from "../../components/ui/icons";
import { Tooltip } from "../../components/ui/Tooltip";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { engineState, sortEngines, supportText, supportView } from "./engines";
import { TEXT_LINK } from "./ui";

/**
 * The engines in one line above the databases: each one's state, version and, when its
 * version has left upstream support, a warning; the Engines tab one click away. Infrastructure
 * the operator glances at, below what they came for.
 */
export function EnginesStrip({ engines }: { engines: readonly Engine[] | undefined }) {
  const t = useT();
  const Warning = ICONS.warning;
  return (
    <nav aria-label={t("databases.strip.label")} className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-1.5 text-13">
      {engines === undefined ? (
        <span aria-hidden="true" className="flex h-5 items-center gap-5">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-3 w-24" />
        </span>
      ) : (
        sortEngines(engines).map((engine) => {
          const { state, label } = engineState(engine);
          const support = supportView(engine.support);
          return (
            <span key={engine.name} className="flex min-w-0 items-center gap-1.5">
              <StatusGlyph state={state} size={10} className={stateTextClass(state)} />
              <span className="font-medium text-fg">{engine.display_name}</span>
              {engine.installed && engine.version ? <Mono tone="muted">{engine.version}</Mono> : null}
              <span className={cx("text-fg-muted", !engine.installed && "text-fg-faint")}>{t(label)}</span>
              {support?.warn && engine.support ? (
                <Tooltip content={supportText(t, engine.support)}>
                  <Link to="/databases/engines" aria-label={supportText(t, engine.support) ?? ""} className="inline-flex rounded-chip text-warn">
                    <Warning aria-hidden="true" className="size-icon-sm" />
                  </Link>
                </Tooltip>
              ) : null}
            </span>
          );
        })
      )}
      <Link to="/databases/engines" className={TEXT_LINK}>
        {t("databases.strip.manage")}
      </Link>
    </nav>
  );
}
