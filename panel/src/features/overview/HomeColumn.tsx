import { Link } from "@tanstack/react-router";

import { Gallery } from "../../components/widgets/Gallery";
import { useCan } from "../../components/page/permissions";
import { SlidingIndicator } from "../../components/ui/SlidingIndicator";
import { ICONS } from "../../components/ui/icons";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { useScopeOffers } from "../../nodes/useNode";
import { cx } from "../../lib/cx";
import { useEditorSession } from "./editorSession";
import { BUILTIN_IDS } from "./panels";
import type { Panel } from "./panels";
import { useHomeAddress } from "./search";
import { usePanels } from "./usePanels";

function panelLine(t: ReturnType<typeof useT>, panel: Panel): string {
  if (panel.builtin) return t(panel.scope === "fleet" ? "overview.panels.fleetLine" : "overview.panels.serverLine");
  return t(panel.scope === "fleet" ? "overview.panels.ownFleetLine" : "overview.panels.ownServerLine");
}

/** The panels of Home, each with what it follows: "This server", "Fleet" and the person's own, and the way to make another. */
function PanelList() {
  const t = useT();
  const address = useHomeAddress();
  const { panels } = usePanels();
  const current = address.panel ?? BUILTIN_IDS[address.scope];
  return (
    <>
      <div className="shrink-0 px-4 pt-4 pb-3">
        <p className="display min-w-0 truncate text-24 text-fg">{t("nav.overview.label")}</p>
      </div>
      <p className="shrink-0 px-5 pb-1.5 text-13 font-medium text-fg-muted">{t("overview.panels.list")}</p>
      <nav aria-label={t("overview.panels.list")} className="relative isolate flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-3 pb-4 scroll-thin">
        {/* The raised piece slides to the panel that is opened, as in the main bar and in every other list on the side. */}
        <SlidingIndicator className="rounded-control bg-surface" />
        {panels.map((panel) => {
          const Icon = panel.scope === "fleet" ? OBJECT_ICONS.fleetSettings : OBJECT_ICONS.server;
          const open = panel.id === current;
          return (
            <Link
              key={panel.id}
              to={panel.scope === "fleet" ? "/fleet" : "/"}
              search={(panel.builtin ? {} : { panel: panel.id }) as never}
              {...(open ? { "aria-current": "page" as const } : {})}
              className={cx("relative flex items-start gap-3 rounded-control px-2.5 py-2 text-13", open ? "text-fg" : "text-fg-muted hover:bg-rail-hover hover:text-fg")}
            >
              <Icon aria-hidden="true" className={cx("mt-0.5 size-icon-md shrink-0", open ? "text-accent-fg" : "text-fg-faint")} />
              <span className="min-w-0">
                <span translate={panel.builtin ? undefined : "no"} className="block truncate text-fg">
                  {panel.name}
                </span>
                <span className="block truncate text-12 text-fg-muted">{panelLine(t, panel)}</span>
              </span>
            </Link>
          );
        })}
        <Link
          to="/"
          search={{ panel: "new" } as never}
          className="relative mt-1 flex items-center gap-3 rounded-control px-2.5 py-2 text-13 text-fg-muted hover:bg-rail-hover hover:text-fg"
        >
          <ICONS.add aria-hidden="true" className="size-icon-md shrink-0 text-accent-fg" />
          {t("overview.panels.newPanel")}
        </Link>
      </nav>
    </>
  );
}

/** While a panel is customized, the column is the gallery: it is the one place its widgets are added from. */
function GalleryColumn() {
  const t = useT();
  const can = useCan();
  const features = useScopeOffers();
  const address = useHomeAddress();
  const session = useEditorSession();
  return (
    <>
      <div className="shrink-0 px-4 pt-4 pb-3">
        <p className="display min-w-0 truncate text-24 text-fg">{t("widgets.gallery.title")}</p>
        <p className="mt-1 text-13 text-fg-muted">{t("widgets.gallery.hint")}</p>
      </div>
      <Gallery
        scope={session?.scope ?? address.scope}
        can={can}
        features={features}
        layout={session?.layout ?? []}
        full={session?.full ?? false}
        onAdd={(widget) => session?.add(widget)}
        {...(session !== null ? { onStartDrag: session.startDrag } : {})}
      />
    </>
  );
}

/**
 * What the frame draws in the second column beside Home: the panels, and, while one is being customized, the
 * gallery of widgets to add to it (it enters as any list one level deeper does). The frame calls this with whether the
 * page is being customized, which both read from the address.
 */
export function HomeColumn({ customising }: { customising: boolean }) {
  return customising ? <GalleryColumn /> : <PanelList />;
}
