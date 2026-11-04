
import { Drawer } from "../../components/ui/Drawer";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { SlidingIndicator } from "../../components/ui/SlidingIndicator";
import { useT } from "../../i18n";
import { AreaLink, useAreas } from "./areas";
import { AreaGroup, GROUP_TITLES, Joint } from "./Joint";

/**
 * Every area of the main bar, in the same titled groups, as the sheet "More" opens on a phone. Two
 * to a row: a phone has the width for a name beside its icon, and a group reads as it does in the
 * bar. Settings and the account are under a joint of their own, because they are how the console is
 * managed and who is signed in, not places it is about. The current area is the raised piece, as in
 * every other list of the frame.
 */
export function AreasSheet({
  open,
  onOpenChange,
  onNavigate,
  onOpenAccount,
  finalFocus,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called when an area is chosen, so the shell can hand focus to the new page. */
  onNavigate: () => void;
  onOpenAccount: () => void;
  finalFocus?: boolean;
}) {
  const t = useT();
  const areas = useAreas();
  return (
    <Drawer open={open} onOpenChange={onOpenChange} placement="bottom" title={t("nav.sheet.title")} finalFocus={finalFocus ?? true}>
      <nav aria-label={t("nav.landmarks.main")} className="relative isolate flex flex-col px-3">
        <SlidingIndicator className="rounded-control bg-surface" />
        {areas.groups.map((group) => (
          <AreaGroup key={group[0]?.entry.id} title={GROUP_TITLES[group[0]?.entry.group ?? ""]} className="[&>ul]:grid [&>ul]:grid-cols-2 [&>ul]:gap-1">
            {group.map((item) => (
              <li key={item.entry.id} className="min-w-0">
                <AreaLink item={item} variant="sheet" onNavigate={onNavigate} />
              </li>
            ))}
          </AreaGroup>
        ))}
        <Joint className="mt-3 mb-2" />
        <ul className="grid grid-cols-2 gap-1">
          <li className="min-w-0">
            <AreaLink item={areas.settings} variant="sheet" onNavigate={onNavigate} />
          </li>
          <li className="min-w-0">
            <button
              type="button"
              onClick={onOpenAccount}
              aria-haspopup="dialog"
              className="group elev-lift flex h-11 w-full cursor-pointer items-center gap-2.5 rounded-control px-3 text-left text-14 font-medium text-fg-muted hover:bg-surface-raised hover:text-fg focus-visible:-outline-offset-2"
            >
              <OBJECT_ICONS.account aria-hidden="true" className="size-icon-lg shrink-0 text-fg-faint group-hover:text-fg-muted" />
              <span className="min-w-0 flex-1 truncate">{t("nav.sheet.account")}</span>
            </button>
          </li>
        </ul>
      </nav>
    </Drawer>
  );
}
