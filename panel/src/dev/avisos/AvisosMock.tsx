import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import { authKeys } from "../../api/queries/auth";
import { Card } from "../../components/ui/Card";
import { Sections } from "../../components/page/Section";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { AttentionRow as HomeRow } from "../../components/widgets/bodies/StateBodies";
import { useT } from "../../i18n";
import { WatchingView } from "../../features/alerts/WatchingSection";
import { AttentionRows } from "../../features/inbox/AttentionRows";
import { ATTENTION, WATCHING, WATCHING_NO_CHANNEL, WATCHING_NO_MONITOR, WATCHING_OFF } from "./fixtures";

/**
 * The design review of items 159 and 161: the real components of the opening of Alerts and of the one list of what needs
 * attention, over specimens of what the server answers. A development page, never part of the build; the captures of
 * these views are what the design is judged by.
 */

function Page({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mx-auto flex w-full max-w-[900px] flex-col gap-6 px-4 py-6 sm:px-0">
      <h1 className="title text-24 text-fg">{title}</h1>
      {children}
    </div>
  );
}

const noop = (): void => undefined;

function AlertsView({ kind }: { kind: string }) {
  const t = useT();
  const data = kind === "nochannel" ? WATCHING_NO_CHANNEL : kind === "off" ? WATCHING_OFF : kind === "monitor" ? WATCHING_NO_MONITOR : WATCHING;
  return (
    <Page title={t("nav.alerts.label")}>
      <Sections>
        <WatchingView data={data} busy={false} onTurnOn={noop} />
        <SurfaceSection title="Rules" icon={OBJECT_ICONS.alert} description="Specimen: the rules of the operator come here, as they always did." />
      </Sections>
    </Page>
  );
}

function BellView({ wide }: { wide: boolean }) {
  const t = useT();
  const [view, setView] = useState<"needs" | "recent">("needs");
  return (
    <div className={wide ? "mx-auto mt-6 w-96 rounded-card border border-border bg-surface-raised p-4" : "w-full bg-surface p-4"}>
      <div className="-mx-4 flex flex-col gap-3">
        <div className="px-4">
          <SegmentedControl
            label={t("inbox.tabs.label")}
            value={view}
            onValueChange={setView}
            options={[
              { value: "needs", label: t("inbox.tabs.needs", { count: ATTENTION.length }) },
              { value: "recent", label: t("inbox.tabs.recent") },
            ]}
          />
        </div>
        <div className={wide ? "max-h-[640px] overflow-y-auto border-y border-border scroll-thin" : "border-y border-border"}>
          <AttentionRows t={t} items={ATTENTION} label="what needs you" />
        </div>
      </div>
    </div>
  );
}

function HomeView({ width }: { width: string }) {
  const t = useT();
  const entries = ATTENTION.slice(0, 5).map((item) => ({ key: item.id, item, server: null }));
  return (
    <div className={`mx-auto mt-6 ${width}`}>
      <Card level={2} padding="sm" title={t("widgets.attention.title")}>
        <div className="widget-body h-56 w-full">
          <ul className="flex h-full flex-col overflow-hidden">
            {entries.map((entry) => (
              <HomeRow key={entry.key} t={t} entry={entry} showServer={false} />
            ))}
          </ul>
        </div>
      </Card>
    </div>
  );
}

/** What the page is asked to draw: `?view=alerts&case=nochannel`, `?view=bell`, `?view=sheet`, `?view=home&width=narrow`. */
export function AvisosMock() {
  // An administrator, so what a role may not do is drawn as well.
  const client = useMemo(() => {
    const seeded = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    seeded.setQueryData(authKeys.session, { authenticated: true, role: "admin", permissions: ["server.read", "settings.manage", "apps.read"] });
    return seeded;
  }, []);
  const search = new URLSearchParams(window.location.search);
  const view = search.get("view") ?? "alerts";
  return (
    <QueryClientProvider client={client}>
      {view === "bell" ? (
        <BellView wide />
      ) : view === "sheet" ? (
        <BellView wide={false} />
      ) : view === "home" ? (
        <HomeView width={search.get("width") === "narrow" ? "w-[342px]" : "w-[864px]"} />
      ) : (
        <AlertsView kind={search.get("case") ?? "default"} />
      )}
    </QueryClientProvider>
  );
}
