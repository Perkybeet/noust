import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { Button } from "../../components/ui/Button";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Popover } from "../../components/ui/Popover";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { getLocale } from "../../app/locale";
import { useNavScope } from "../../app/useNavScope";
import { loadNamespaces } from "../../i18n/catalogs";
import { useT } from "../../i18n";
import { useServerList } from "../../nodes/servers";
import { useNode } from "../../nodes/useNode";
import { useMe } from "./feeds";
import { queuedCount, runningCount } from "./model";
import { FleetPanel, ServerPanel } from "./TaskPanel";
import { useTaskState } from "./store";

/**
 * The top bar's entry to the task centre (item 114): an icon that says how many tasks are running, in the shape
 * and colour of work in progress, and the panel under it. Yours first (running, then waiting), then what the
 * server does that you did not start, then what ended lately with its result; each can show its last lines of
 * log and leads to its page, and a running one can be pinned to the foot of the page. On "All servers" the same
 * panel is grouped by server.
 */
export function TaskCentreButton() {
  const t = useT();
  const [open, setOpen] = useState(false);
  const scope = useNavScope();
  const { node } = useNode();
  const state = useTaskState();
  const me = useMe();
  const { hostname, version } = useServerList();
  const fleet = scope === "fleet";

  // What the bar counts: the server on screen's, or every server that answered the central.
  const counted = useMemo(
    () => [...state.tasks.values()].filter((task) => (fleet ? state.servers.has(task.server ?? "") : task.server === node)),
    [state.tasks, state.servers, fleet, node],
  );
  const running = runningCount(counted);
  const queued = queuedCount(counted);
  const here = useMemo(() => counted.filter((task) => task.server === node), [counted, node]);
  const close = (): void => {
    setOpen(false);
  };
  // Opening it for the first time should not draw English: its texts are asked for when a hand or a key is near.
  const prepare = (): void => {
    void loadNamespaces(getLocale(), ["tasks", "serverText"]);
  };

  const button = (
    <Button variant="ghost" onPointerEnter={prepare} onFocus={prepare} aria-label={running + queued > 0 ? t("shell.tasks.label", { running, queued }) : t("shell.tasks.idle")} className="px-2" data-tasks={running > 0 ? "running" : "idle"}>
      <OBJECT_ICONS.work aria-hidden="true" />
      {running > 0 ? (
        <span className="inline-flex items-center gap-1 text-12 font-medium text-warn">
          <StatusGlyph state="deploying" size={10} />
          <span aria-hidden="true" className="tabular-nums">
            {running}
          </span>
        </span>
      ) : null}
    </Button>
  );

  // The words of the panel are asked for only once it is open: the icon is in the frame of every page, and its closed
  // state is the chrome's (`shell.tasks`), so a page does not wait for the task centre's namespace to be drawn.
  const panelWords = open
    ? {
        title: t("tasks.title"),
        description: fleet ? t("tasks.summary.fleet", { count: state.servers.size }) : t("tasks.summary.server", { server: node ?? hostname ?? "", running, queued }),
        action: (
          <div className="flex items-center gap-1">
            <Link to="/activity/tasks" onClick={close} className="rounded-control px-2 py-1 text-12 font-medium text-accent-fg hover:bg-accent-soft">
              {t("tasks.history")}
            </Link>
            <IconButton label={t("common.words.close")} icon={<ICONS.close />} size="sm" onClick={close} />
          </div>
        ),
      }
    : null;

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      align="end"
      trigger={button}
      {...(panelWords !== null ? { title: panelWords.title, description: panelWords.description, headerAction: panelWords.action } : {})}
      className="w-96"
    >
      <div className="-mx-4 -mb-4 max-h-128 overflow-y-auto scroll-thin">
        {open ? fleet ? <FleetPanel state={state} target={version} onNavigate={close} /> : <ServerPanel tasks={here} me={me} onNavigate={close} /> : null}
      </div>
    </Popover>
  );
}
