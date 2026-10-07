import { Menu as BaseMenu } from "@base-ui/react/menu";
import { useNavigate } from "@tanstack/react-router";
import { Check, ChevronsUpDown, Settings2, Users } from "lucide-react";

import { announce } from "../../app/Announcer";
import { Mono } from "../../components/ui/Mono";
import { POPUP_MOTION } from "../../components/ui/Tooltip";
import { reportActionError } from "../apps/useAppActions";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { teamRoleLabel } from "./roles";
import { useOrganization } from "./useOrganization";

/** The radio value for "every team": no team slug can be one (slugs are [a-z0-9-]). */
const EVERY_TEAM = "@all";

const ITEM = cx(
  "flex min-h-11 cursor-pointer items-center gap-2.5 rounded-control px-2 py-1.5 text-13 outline-none select-none",
  "text-fg data-highlighted:bg-surface-hover",
);

const GROUP_LABEL = "px-2 pt-1.5 pb-1 text-12 font-medium text-fg-muted select-none";

function ItemCheck() {
  return (
    <span className="flex size-4 shrink-0 items-center justify-center">
      <BaseMenu.RadioItemIndicator>
        <Check aria-hidden="true" className="size-4 text-fg" />
      </BaseMenu.RadioItemIndicator>
    </span>
  );
}

/**
 * The team the operator works in, in the top bar: which team's applications, databases and
 * sites the lists show and where a new one goes. A menu of radio items (arrow keys, Home and
 * End, typeahead, Escape), so a screen reader hears which is checked, and each change is
 * announced. An administrator also has "All teams"; so has anyone in several teams ("All my
 * teams"). The choice is the account's, remembered by the server.
 *
 * Not drawn on a server nobody organised in teams: one team called "default" has nothing to
 * switch, and the console looks exactly as it always did.
 */
export function TeamSwitcher({ className }: { className?: string }) {
  const t = useT();
  const navigate = useNavigate();
  const org = useOrganization();
  if (!org.teamsMatter) return null;

  const several = org.teams.length > 1;
  const everyWords = org.seesAll ? t("teams.switcher.all") : t("teams.switcher.allMine");
  const current = org.teams.find((team) => team.slug === org.active);
  const name = current?.name ?? everyWords;
  const label = current === undefined ? t("teams.switcher.triggerAll", { name: everyWords }) : t("teams.switcher.trigger", { name: current.name });
  const value = org.active ?? EVERY_TEAM;

  const choose = (chosen: unknown): void => {
    if (typeof chosen !== "string" || chosen === value) return;
    const next = chosen === EVERY_TEAM ? null : chosen;
    const words = next === null ? t("teams.switcher.switchedAll") : t("teams.switcher.switched", { name: org.teams.find((team) => team.slug === next)?.name ?? next });
    org.choose(next).then(
      () => {
        announce(words);
      },
      (error: unknown) => {
        reportActionError(t("teams.switcher.failed"), error);
      },
    );
  };

  return (
    <BaseMenu.Root>
      <BaseMenu.Trigger
        aria-label={label}
        data-testid="team-switcher"
        className={cx(
          "flex h-control-md max-w-60 min-w-0 shrink cursor-pointer items-center gap-1.5 rounded-control border border-border-strong bg-surface pr-1.5 pl-2.5 text-13 text-fg shadow-raised",
          "transition-colors duration-(--duration-fast) ease-out hover:bg-surface-hover",
          className,
        )}
      >
        <Users aria-hidden="true" className="size-icon-sm shrink-0 text-fg-muted" />
        <span className="min-w-0 truncate font-medium max-sm:sr-only">{name}</span>
        <ChevronsUpDown aria-hidden="true" className="size-icon-sm shrink-0 text-fg-faint max-sm:hidden" />
      </BaseMenu.Trigger>
      <BaseMenu.Portal>
        <BaseMenu.Positioner side="bottom" align="start" sideOffset={4} className="z-overlay outline-none">
          <BaseMenu.Popup
            className={cx(
              // design-exception: hand-surface a radio menu has no kit component; this is Menu's own popup surface
              "max-h-96 w-72 overflow-y-auto rounded-card border border-border bg-surface-raised p-1 text-fg shadow-overlay outline-none scroll-thin",
              POPUP_MOTION,
            )}
          >
            <BaseMenu.RadioGroup value={value} onValueChange={choose}>
              <BaseMenu.Group>
                <BaseMenu.GroupLabel className={GROUP_LABEL}>{t("teams.switcher.heading")}</BaseMenu.GroupLabel>
                {several ? (
                  <BaseMenu.RadioItem value={EVERY_TEAM} closeOnClick className={ITEM}>
                    <ItemCheck />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-medium">{everyWords}</span>
                      <span className="text-12 text-fg-muted">{t("teams.switcher.allDescription")}</span>
                    </span>
                  </BaseMenu.RadioItem>
                ) : null}
                {org.teams.map((team) => (
                  <BaseMenu.RadioItem key={team.slug} value={team.slug} closeOnClick className={ITEM}>
                    <ItemCheck />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-medium">{team.name}</span>
                      <span className="flex min-w-0 items-center gap-1.5 text-12 text-fg-muted">
                        <Mono tone="muted" truncate>
                          {team.slug}
                        </Mono>
                        {team.role !== null && team.role !== undefined ? (
                          <>
                            <span aria-hidden="true">·</span>
                            <span className="truncate">{teamRoleLabel(t, team.role)}</span>
                          </>
                        ) : null}
                      </span>
                    </span>
                  </BaseMenu.RadioItem>
                ))}
              </BaseMenu.Group>
            </BaseMenu.RadioGroup>
            <div className="mt-1 border-t border-border pt-1">
              {current !== undefined ? (
                <BaseMenu.Item
                  className={cx(ITEM, "min-h-8")}
                  onClick={() => void navigate({ to: "/teams/$team", params: { team: current.slug } })}
                >
                  <Settings2 aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
                  {t("teams.switcher.settings", { name: current.name })}
                </BaseMenu.Item>
              ) : null}
              <BaseMenu.Item className={cx(ITEM, "min-h-8")} onClick={() => void navigate({ to: "/teams" })}>
                <Users aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
                {t("teams.switcher.manage")}
              </BaseMenu.Item>
            </div>
          </BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.Root>
  );
}
