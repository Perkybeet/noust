import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { LayoutGrid } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { CommandHint } from "../../components/page/CommandHint";
import { DashboardPage } from "../../components/page/DashboardPage";
import { Sections } from "../../components/page/Section";
import { Button, buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { HelpPopover } from "../../components/ui/HelpPopover";
import { LG_UP, useMediaQuery } from "../../components/ui/useMediaQuery";
import { ICONS } from "../../components/ui/icons";
import { OBJECT_ICONS, VERB_ICONS } from "../../components/ui/objectIcons";
import { faceFor, newPlaced } from "../../components/widgets/catalog";
import { Gallery } from "../../components/widgets/Gallery";
import { GallerySheet } from "../../components/widgets/GallerySheet";
import type { Placed } from "../../components/widgets/model";
import { PanelSkeleton } from "../../components/widgets/PanelSkeleton";
import type { PanelScope } from "../../components/widgets/types";
import { useCan } from "../../components/page/permissions";
import { useWidgetBoard } from "../../components/widgets/useWidgetBoard";
import { DragGhost, WidgetGrid } from "../../components/widgets/WidgetGrid";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { setEditorSession } from "./editorSession";
import { FirstSteps } from "./FirstSteps";
import { NewPanelPage } from "./NewPanelPage";
import { usePanelActions } from "./PanelActions";
import { BUILTIN_IDS, defaultLayout } from "./panels";
import type { Panel } from "./panels";
import { useScopedHome } from "./useHome";
import { usePanels } from "./usePanels";
import type { Panels } from "./usePanels";

function NewAppLink({ scope }: { scope: PanelScope }) {
  const t = useT();
  return (
    <Link to="/apps/new" {...(scope === "fleet" ? { search: { node: undefined } } : {})} className={buttonClassName("primary")}>
      <ICONS.add aria-hidden="true" />
      {t("overview.newApplication")}
    </Link>
  );
}

export interface HomePageProps {
  /** Which address Home was opened at: `/` is the panels of one server, `/fleet` those of the whole fleet. */
  scope: PanelScope;
  /** The panel the address names, or undefined for the one that comes with the console. `new` is the page that makes one. */
  panelId?: string | undefined;
  /** The panel is being arranged: the column is the gallery, and each widget has its handle, its size and its remove button. */
  customising: boolean;
}

/**
 * Home, the first page: a panel of widgets. "This server" and "Fleet" come ready, and a person adds their own; each is
 * a grid of widgets that read what the console reads, so a person sees what they need to know without going to
 * ask for it. "Customize" turns the column beside the page into the gallery and puts a handle, a size and a remove
 * button on every widget; what is changed is kept at once. An empty installation gets its first steps instead, never a
 * board of nothing.
 */
export function HomePage({ scope, panelId, customising }: HomePageProps) {
  if (panelId === "new") return <NewPanelPage scope={scope} />;
  return <PanelPage scope={scope} panelId={panelId} customising={customising} />;
}

/** Finds the panel the address names, and waits for the ones that are not known yet. */
function PanelPage({ scope, panelId, customising }: HomePageProps) {
  const t = useT();
  const can = useCan();
  const panels = usePanels();
  const wanted = panelId ?? BUILTIN_IDS[scope];
  const panel = panels.panels.find((candidate) => candidate.id === wanted) ?? panels.panels.find((candidate) => candidate.id === BUILTIN_IDS[scope]) ?? panels.panels[0];
  if (panel === undefined) return null;
  if (!panels.ready) {
    return (
      // The line under the title is there already, so the widgets do not move down when the panel is known.
      <DashboardPage header={{ title: panel.name, meta: <Follows panel={panel} /> }}>
        <PanelSkeleton layout={defaultLayout(panel.scope, can)} columns={4} title={t("nav.overview.label")} />
      </DashboardPage>
    );
  }
  // Keyed by the panel and the mode: what is being dragged is a draft, and a new panel or a finished customization starts from what is kept.
  return <PanelView key={`${panel.id}:${String(customising)}`} panel={panel} panels={panels} customising={customising} />;
}

/** The header's meta line: what the panel follows, in words and never by a tint (6.11). */
function Follows({ panel }: { panel: Panel }) {
  const t = useT();
  const { node } = useNode();
  const { data: hostname } = useQuery({ ...sessionQuery(), select: (session) => session.hostname });
  const home = useScopedHome(panel.scope);
  const Icon = panel.scope === "fleet" ? OBJECT_ICONS.fleetSettings : OBJECT_ICONS.server;
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon aria-hidden="true" className="size-icon-sm shrink-0" />
      {panel.scope === "fleet" ? (
        home.servers === undefined ? (
          t("overview.panels.wholeFleetLoading")
        ) : (
          t("overview.panels.wholeFleet", { count: home.servers.length })
        )
      ) : (
        t.rich("overview.panels.follows", { server: <span translate="no">{node ?? hostname ?? ""}</span> })
      )}
    </span>
  );
}

function PanelView({ panel, panels, customising }: { panel: Panel; panels: Panels; customising: boolean }) {
  const t = useT();
  const can = useCan();
  const phone = !useMediaQuery(LG_UP);
  const home = useScopedHome(panel.scope);
  // What is being arranged is kept here as the person moves it: the order the board works with is the one they made,
  // and not the one the stored document reads back as (a narrow widget may sit above a wide one it comes after).
  const [draft, setDraft] = useState<readonly Placed[] | null>(null);
  const layout = draft ?? panel.layout;
  const onLayoutChange = (next: readonly Placed[]): void => {
    setDraft(next);
    panels.save(panel, next);
  };
  const faceOf = useMemo(() => faceFor(t, panel.scope), [t, panel.scope]);
  const board = useWidgetBoard({ layout, onLayoutChange, faceOf, phone });
  const [sheet, setSheet] = useState(false);
  const full = layout.length >= panels.limits.widgets;
  const route = panel.scope === "fleet" ? "/fleet" : "/";
  const here = panel.builtin ? {} : { panel: panel.id };
  const navigate = useNavigate();
  const actions = usePanelActions(panel, panels, () => {
    // A panel that is gone leaves its address with it: the one that comes with the console is where its person lands.
    void navigate({ to: route, search: {} as never });
  });

  // The gallery is in the column, which is another part of the frame: it is told what the panel holds and how to add to it.
  const { add, startAdd } = board;
  useEffect(() => {
    if (!customising) return;
    setEditorSession({
      scope: panel.scope,
      layout,
      full,
      add: (widget) => add(newPlaced(widget, layout)),
      startDrag: (widget, event) => startAdd(newPlaced(widget, layout), event),
    });
    return () => setEditorSession(null);
  }, [customising, panel.scope, layout, full, add, startAdd]);

  const empty = home.empty && !customising;
  const header = {
    title: customising ? t("overview.panels.customizeTitle", { name: panel.name }) : panel.name,
    meta: <Follows panel={panel} />,
    ...(actions.overflow !== undefined ? { overflow: actions.overflow } : {}),
    ...(empty
      ? { primaryAction: <NewAppLink scope={panel.scope} /> }
      : customising
        ? {
            secondaryActions: (
              <>
                {phone ? (
                  <Button icon={<ICONS.add />} onClick={() => setSheet(true)}>
                    {t("overview.panels.addWidget")}
                  </Button>
                ) : null}
                <Button
                  icon={<VERB_ICONS.reset />}
                  onClick={() => {
                    setDraft(null);
                    panels.reset(panel);
                  }}
                >
                  {t("overview.panels.reset")}
                </Button>
              </>
            ),
            primaryAction: (
              <Link to={route} search={here as never} className={buttonClassName("primary")}>
                {t("common.words.done")}
              </Link>
            ),
          }
        : {
            secondaryActions: (
              <Link to={route} search={{ ...here, customise: true } as never} className={buttonClassName("secondary")}>
                <LayoutGrid aria-hidden="true" />
                {t("overview.panels.customize")}
              </Link>
            ),
            primaryAction: <NewAppLink scope={panel.scope} />,
          }),
  };
  const footer = <CommandHint label={t("common.words.fromTerminal")} command={empty ? "noust create --temporary -s https://github.com/you/app" : panel.scope === "fleet" ? "noust fleet apps" : "noust health"} />;

  if (empty) {
    return (
      <DashboardPage header={header} footer={footer}>
        <Sections>
          <FirstSteps backupsScheduled={home.backupsScheduled} />
        </Sections>
      </DashboardPage>
    );
  }

  return (
    <DashboardPage header={header} footer={footer}>
      {customising ? (
        <div className="-mt-4 mb-4 flex items-center gap-1 text-13 text-fg-muted">
          <p>{t("overview.panels.moveHint")}</p>
          <HelpPopover label={t("overview.panels.withoutMouse")} title={t("overview.panels.withoutMouse")}>
            <p>{t("overview.panels.withoutMouseHelp")}</p>
          </HelpPopover>
        </div>
      ) : null}
      {layout.length === 0 && !customising ? (
        <EmptyState
          variant="inline"
          title={t("overview.panels.emptyIdle")}
          description={
            <Link to={route} search={{ ...here, customise: true } as never} className="font-medium text-accent-fg hover:underline">
              {t("overview.panels.customize")}
            </Link>
          }
        />
      ) : (
        <WidgetGrid board={board} layout={layout} onLayoutChange={onLayoutChange} faceOf={faceOf} editing={customising} />
      )}
      {layout.length === 0 && customising ? <p className="mt-6 text-14 text-fg-muted">{t(phone ? "overview.panels.emptyPanelSheet" : "overview.panels.emptyPanel")}</p> : null}
      {board.drag !== null ? <DragGhost drag={board.drag} faceOf={faceOf} /> : null}
      {phone && customising ? (
        <GallerySheet open={sheet} onOpenChange={setSheet} title={t("widgets.gallery.title")}>
          <Gallery
            scope={panel.scope}
            can={can}
            layout={layout}
            full={full}
            onAdd={(widget) => {
              board.add(newPlaced(widget, layout));
              setSheet(false);
            }}
          />
        </GallerySheet>
      ) : null}
      {actions.dialogs}
    </DashboardPage>
  );
}
