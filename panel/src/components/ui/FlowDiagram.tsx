import { CircleDot, CornerUpRight, Folder, Network, Plug, Route, Server, ServerCog } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useId, useLayoutEffect, useMemo, useState } from "react";
import type { CSSProperties, KeyboardEvent } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { DataTable } from "./DataTable";
import type { Column } from "./DataTable";
import { Disclosure } from "./Disclosure";
import { Mono } from "./Mono";
import { connectedPath, layoutFlow, pathEdges } from "./flowDiagram.layout";
import type { FlowLayout, PlacedEdge, PlacedNode } from "./flowDiagram.layout";
import type { FlowEdge, FlowLayer, FlowNode, FlowNodeKind, FlowNodeState } from "./flowDiagram.types";
import { StatusGlyph, stateTextClass } from "./StatusPill";
import type { Status } from "./StatusPill";
import { REDUCED_MOTION, SM_UP, useMediaQuery } from "./useMediaQuery";

export interface FlowDiagramProps {
  layers: readonly FlowLayer[];
  edges: readonly FlowEdge[];
  /** The diagram's accessible name: "How proggest.es answers". Also names its table. */
  label: string;
  /**
   * A route to show, as the ids of its elements in order (from `POST /route`): only its
   * connections move, and the summary says it in words.
   */
  highlight?: readonly string[];
  /** The element the operator points at or focuses, or null when they leave it. */
  onNodeFocus?: (id: string | null) => void;
  /** The written summary under the diagram. Defaults to one counted from the data. */
  summary?: string;
  /** The same connections as a table, behind a disclosure (DESIGN A-7). On by default. */
  table?: boolean;
  /** Moving dashes along the connections. Never with reduced motion, whatever this says. */
  animated?: boolean;
  className?: string;
}

const PAD = 16;
const HEADER = 36;
const COLUMN_GAP = 48;
// A node narrower than this truncates every path; wider than that wastes the line.
const MIN_NODE = 160;
const MAX_NODE = 240;

/**
 * The width of an element, kept current as it resizes: the diagram fills the column it is
 * given instead of scrolling sideways in a wide one or squeezing in a narrow one.
 */
function useWidth(element: HTMLElement | null): number {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    if (!element) return;
    const read = (): void => setWidth(element.clientWidth);
    read();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(read);
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);
  return width;
}

const KIND_ICON: Record<FlowNodeKind, LucideIcon> = {
  listener: Plug,
  server: Server,
  location: Route,
  upstream: Network,
  "upstream-server": ServerCog,
  static: Folder,
  redirect: CornerUpRight,
  other: CircleDot,
};

// An outline per kind as well as an icon, so a column reads as what it is at a glance.
const KIND_SHAPE: Record<FlowNodeKind, string> = {
  listener: "rounded-pill px-3.5",
  server: "rounded-card",
  location: "rounded-chip",
  upstream: "rounded-card",
  "upstream-server": "rounded-control",
  static: "rounded-control",
  redirect: "rounded-control",
  other: "rounded-control",
};

const STATE_STATUS: Record<Exclude<FlowNodeState, "none">, Status> = {
  ok: "running",
  fail: "failed",
  unknown: "unknown",
};

interface Lit {
  nodes: ReadonlySet<string>;
  edges: ReadonlySet<string>;
}

function useNodeWords() {
  const t = useT();
  const stateWord = (node: FlowNode): string | null => {
    const state = node.state ?? "none";
    if (state === "none") return null;
    return node.stateLabel ?? t(`common.flowDiagram.state.${state}`);
  };
  const name = (node: FlowNode): string => {
    const kind = t(`common.flowDiagram.kind.${node.kind}`);
    const state = stateWord(node);
    const { label, detail } = node;
    if (detail !== undefined && state !== null) return t("common.flowDiagram.nodeNameFull", { kind, label, detail, state });
    if (detail !== undefined) return t("common.flowDiagram.nodeNameWithDetail", { kind, label, detail });
    if (state !== null) return t("common.flowDiagram.nodeNameWithState", { kind, label, state });
    return t("common.flowDiagram.nodeName", { kind, label });
  };
  return { stateWord, name };
}

interface NodeCardProps {
  node: FlowNode;
  active: boolean;
  pinned: boolean;
  style?: CSSProperties;
  onEnter: (id: string) => void;
  onLeave: () => void;
  onPress: (id: string) => void;
  onEscape: () => void;
}

function NodeCard({ node, active, pinned, style, onEnter, onLeave, onPress, onEscape }: NodeCardProps) {
  const { stateWord, name } = useNodeWords();
  const Icon = KIND_ICON[node.kind];
  const state = node.state ?? "none";
  const word = stateWord(node);
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>): void => {
    if (event.key === "Escape" && pinned) {
      event.stopPropagation();
      onEscape();
    }
  };
  return (
    <button
      type="button"
      data-node={node.id}
      data-kind={node.kind}
      {...(active ? { "data-active": "" } : {})}
      aria-label={name(node)}
      aria-pressed={pinned}
      onFocus={() => onEnter(node.id)}
      onBlur={onLeave}
      onPointerEnter={() => onEnter(node.id)}
      onPointerLeave={onLeave}
      onClick={() => onPress(node.id)}
      onKeyDown={onKeyDown}
      style={style}
      className={cx(
        "flex min-w-0 cursor-pointer flex-col justify-center gap-0.5 border bg-surface px-2.5 py-1.5 text-left",
        "transition-colors duration-(--duration-fast) ease-out hover:bg-surface-hover",
        KIND_SHAPE[node.kind],
        pinned ? "border-accent" : active ? "border-fg" : "border-border-strong",
      )}
    >
      <span className="flex min-w-0 items-center gap-1.5">
        <Icon aria-hidden="true" className="size-icon-sm shrink-0 text-fg-muted" />
        <span translate="no" className="mono min-w-0 truncate text-12 text-fg">
          {node.label}
        </span>
      </span>
      {word !== null || node.detail !== undefined ? (
        <span className="flex min-w-0 items-center gap-1 text-12">
          {word !== null && state !== "none" ? (
            <>
              <StatusGlyph state={STATE_STATUS[state]} size={10} className={stateTextClass(STATE_STATUS[state])} />
              <span className={cx("shrink-0 font-medium", stateTextClass(STATE_STATUS[state]))}>{word}</span>
              {node.detail !== undefined ? (
                <span aria-hidden="true" className="text-fg-faint">
                  ·
                </span>
              ) : null}
            </>
          ) : null}
          {node.detail !== undefined ? <span className="min-w-0 truncate text-fg-muted">{node.detail}</span> : null}
        </span>
      ) : null}
    </button>
  );
}

interface EdgeProps {
  placed: PlacedEdge;
  active: boolean;
  dimmed: boolean;
  flowing: boolean;
}

/**
 * One connection: a quiet line, and over it the dashes that move. A WebSocket is two rails
 * (a wide stroke with the ground drawn down its middle); TLS moves twice as fast.
 */
function Edge({ placed, active, dimmed, flowing }: EdgeProps) {
  const { d, edge } = placed;
  const tone = active ? "text-fg" : dimmed ? "text-border" : "text-border-strong";
  return (
    <g
      data-edge={placed.id}
      {...(active ? { "data-active": "" } : {})}
      {...(edge.websocket ? { "data-websocket": "" } : {})}
      {...(edge.tls ? { "data-tls": "" } : {})}
      className={tone}
      fill="none"
    >
      {edge.websocket ? (
        <>
          <path data-rail="" d={d} stroke="currentColor" strokeWidth={active ? 6 : 5} />
          <path data-rail="" d={d} className="stroke-bg-sunken" strokeWidth={active ? 3 : 2.5} />
        </>
      ) : (
        <path d={d} stroke="currentColor" strokeWidth={active ? 2 : 1.25} />
      )}
      <path
        d={d}
        stroke="currentColor"
        strokeWidth={edge.websocket ? 2 : active ? 2 : 1.5}
        strokeDasharray="4 12"
        strokeLinecap="round"
        className={cx(active ? "text-fg" : "text-fg-muted", flowing && (edge.tls ? "animate-flow-fast" : "animate-flow"))}
      />
    </g>
  );
}

/** Ids of the nodes each node leads to, in the order of the edges. */
function targetsOf(edges: readonly FlowEdge[]): Map<string, string[]> {
  const targets = new Map<string, string[]>();
  for (const edge of edges) {
    const list = targets.get(edge.from);
    if (!list) targets.set(edge.from, [edge.to]);
    else if (!list.includes(edge.to)) list.push(edge.to);
  }
  return targets;
}

/** A layer's placed nodes from top to bottom, with the caption that opens each group. */
function columnOf(layout: FlowLayout, layer: number): { caption: string | null; placed: PlacedNode }[] {
  const placed = layout.nodes.filter((entry) => entry.layer === layer).sort((a, b) => a.y - b.y);
  return placed.map((entry, index) => ({
    caption: entry.node.group !== undefined && entry.node.group !== placed[index - 1]?.node.group ? entry.node.group : null,
    placed: entry,
  }));
}

interface Row {
  id: string;
  from: FlowNode;
  to: FlowNode;
  edge: FlowEdge;
}

/**
 * How requests travel through a web server: ports, server blocks, locations, destinations
 * and backends, in fixed columns from left to right, with the traffic drawn as dashes moving
 * along each connection. Pointing at or focusing an element lights its whole way; pressing it
 * keeps it lit. Under it, a written summary and the same connections as a table; on a phone,
 * a list per column. Read only: it shows, it does not edit.
 */
export function FlowDiagram({
  layers,
  edges,
  label,
  highlight,
  onNodeFocus,
  summary,
  table = true,
  animated = true,
  className,
}: FlowDiagramProps) {
  const t = useT();
  const { stateWord } = useNodeWords();
  const wide = useMediaQuery(SM_UP);
  const reduced = useMediaQuery(REDUCED_MOTION);
  const summaryId = useId();
  const [hovered, setHovered] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);

  const [box, setBox] = useState<HTMLDivElement | null>(null);
  const boxWidth = useWidth(box);
  const columnsCount = Math.max(layers.length, 1);
  const nodeWidth = Math.min(
    MAX_NODE,
    Math.max(MIN_NODE, Math.floor((boxWidth - PAD * 2 - (columnsCount - 1) * COLUMN_GAP) / columnsCount)),
  );
  const layout = useMemo(() => layoutFlow(layers, edges, { nodeWidth, columnGap: COLUMN_GAP }), [layers, edges, nodeWidth]);
  const nodesById = useMemo(() => new Map(layout.nodes.map((placed) => [placed.node.id, placed.node])), [layout]);
  const targets = useMemo(() => targetsOf(layout.edges.map((placed) => placed.edge)), [layout]);
  const route = useMemo(() => (highlight && highlight.length > 0 ? highlight : null), [highlight]);
  const routeEdges = useMemo(() => (route ? pathEdges(route, edges) : null), [route, edges]);

  const focus = hovered ?? pinned;
  const lit: Lit | null = useMemo(() => {
    if (focus !== null) return connectedPath(focus, edges);
    if (route && routeEdges) return { nodes: new Set(route), edges: routeEdges };
    return null;
  }, [focus, edges, route, routeEdges]);

  const enter = (id: string): void => {
    setHovered(id);
    onNodeFocus?.(id);
  };
  const leave = (): void => {
    setHovered(null);
    onNodeFocus?.(null);
  };
  const press = (id: string): void => setPinned((current) => (current === id ? null : id));
  const unpin = (): void => setPinned(null);

  const list = new Intl.ListFormat(t.locale, { type: "conjunction" });
  const allNodes = layout.nodes.map((placed) => placed.node);
  const failing = allNodes.filter((node) => node.state === "fail");
  const checked = allNodes.some((node) => node.state !== undefined && node.state !== "none");
  const written =
    summary ??
    [
      t("common.flowDiagram.summary", { count: layout.edges.length, nodes: allNodes.length, layers: layers.length }),
      failing.length > 0
        ? t("common.flowDiagram.failing", { count: failing.length, names: list.format(failing.map((node) => node.label)) })
        : checked
          ? t("common.flowDiagram.allResponding")
          : null,
      route
        ? t("common.flowDiagram.highlighted", {
            path: route.map((id) => nodesById.get(id)?.label ?? id).join(" → "),
          })
        : null,
    ]
      .filter((sentence): sentence is string => sentence !== null)
      .join(" ");

  const card = (node: FlowNode, style?: CSSProperties) => (
    <NodeCard
      node={node}
      active={lit?.nodes.has(node.id) ?? false}
      pinned={pinned === node.id}
      {...(style ? { style } : {})}
      onEnter={enter}
      onLeave={leave}
      onPress={press}
      onEscape={unpin}
    />
  );

  const width = layout.width + PAD * 2;
  const height = layout.height + PAD * 2;

  const diagram = wide ? (
    <div className="relative isolate max-h-160 overflow-auto rounded-card border border-border bg-bg-sunken scroll-thin">
      <div className="sticky top-0 z-sticky border-b border-border bg-bg-sunken" style={{ width, height: HEADER }}>
        {layout.columns.map((column) => (
          <p
            key={column.layer.id}
            aria-hidden="true"
            className="absolute top-0 flex h-full items-center truncate text-12 font-medium text-fg-muted"
            style={{ left: column.x + PAD, width: column.width }}
          >
            {column.layer.title}
          </p>
        ))}
      </div>
      <div className="relative" style={{ width, height }}>
        <svg data-flow-edges="" aria-hidden="true" width={width} height={height} className="absolute inset-0 overflow-visible">
          <g transform={`translate(${String(PAD)} ${String(PAD)})`}>
            {layout.edges.map((placed) => (
              <Edge
                key={placed.id}
                placed={placed}
                active={lit?.edges.has(placed.id) ?? false}
                dimmed={lit !== null && !lit.edges.has(placed.id)}
                flowing={animated && !reduced && (routeEdges ? routeEdges.has(placed.id) : true)}
              />
            ))}
          </g>
        </svg>
        {layout.captions.map((caption) => (
          <p
            key={`${String(caption.layer)}:${caption.label}`}
            aria-hidden="true"
            className="absolute flex items-end truncate pb-1 text-12 text-fg-faint"
            style={{ left: caption.x + PAD, top: caption.y + PAD, width: caption.width, height: caption.height }}
          >
            {caption.label}
          </p>
        ))}
        {layout.columns.map((column, index) => (
          <ul key={column.layer.id} aria-label={column.layer.title}>
            {columnOf(layout, index).map(({ placed }) => (
              <li key={placed.node.id}>
                {card(placed.node, { position: "absolute", left: placed.x + PAD, top: placed.y + PAD, width: placed.width, height: placed.height })}
              </li>
            ))}
          </ul>
        ))}
      </div>
    </div>
  ) : (
    <div className="flex flex-col gap-6">
      {layout.columns.map((column, index) => (
        <div key={column.layer.id} className="flex min-w-0 flex-col gap-2">
          <p aria-hidden="true" className="text-12 font-medium text-fg-muted">
            {column.layer.title}
          </p>
          <ul aria-label={column.layer.title} className="flex flex-col gap-2">
            {columnOf(layout, index).map(({ caption, placed }) => {
              const next = (targets.get(placed.node.id) ?? []).map((id) => nodesById.get(id)?.label ?? id);
              return (
                <li key={placed.node.id} className="flex min-w-0 flex-col gap-1">
                  {caption !== null ? (
                    <span aria-hidden="true" className="pt-2 text-12 text-fg-faint">
                      {caption}
                    </span>
                  ) : null}
                  {card(placed.node)}
                  {next.length > 0 ? (
                    <span className="pl-2.5 text-12 break-words text-fg-muted">
                      {t.rich("common.flowDiagram.leadsTo", { targets: <Mono>{list.format(next)}</Mono> })}
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );

  const connection = (edge: FlowEdge): string =>
    edge.websocket && edge.tls
      ? t("common.flowDiagram.tlsWebsocket")
      : edge.websocket
        ? t("common.flowDiagram.websocket")
        : edge.tls
          ? t("common.flowDiagram.tls")
          : t("common.flowDiagram.http");

  const rows: Row[] = layout.edges.flatMap((placed) => {
    const from = nodesById.get(placed.edge.from);
    const to = nodesById.get(placed.edge.to);
    return from && to ? [{ id: placed.id, from, to, edge: placed.edge }] : [];
  });

  const nodeCell = (node: FlowNode) => (
    <span className="flex min-w-0 flex-col">
      <span translate="no" className="mono truncate">
        {node.label}
      </span>
      <span className="text-12 text-fg-muted">{t(`common.flowDiagram.kind.${node.kind}`)}</span>
    </span>
  );

  const columns: Column<Row>[] = [
    { id: "from", header: t("common.flowDiagram.from"), cell: (row) => nodeCell(row.from) },
    { id: "to", header: t("common.flowDiagram.to"), cell: (row) => nodeCell(row.to) },
    { id: "connection", header: t("common.flowDiagram.connection"), cell: (row) => connection(row.edge), width: "w-40" },
    {
      id: "state",
      header: t("common.flowDiagram.stateColumn"),
      width: "w-44",
      cell: (row) => {
        const state = row.to.state ?? "none";
        if (state === "none") return <span className="text-fg-muted">{t("common.flowDiagram.noState")}</span>;
        return (
          <span className={cx("inline-flex items-center gap-1.5 font-medium", stateTextClass(STATE_STATUS[state]))}>
            <StatusGlyph state={STATE_STATUS[state]} size={10} />
            {stateWord(row.to)}
          </span>
        );
      },
    },
  ];

  return (
    <div ref={setBox} className={cx("flex min-w-0 flex-col gap-3", className)}>
      <figure aria-label={label} aria-describedby={summaryId} className="flex min-w-0 flex-col gap-3">
        {diagram}
        <figcaption className="flex max-w-measure flex-col gap-1 text-13 text-pretty">
          <span id={summaryId} className="text-fg">
            {written}
          </span>
          <span className="text-12 text-fg-muted">{t("common.flowDiagram.legend")}</span>
        </figcaption>
      </figure>
      {table ? (
        <Disclosure label={t("common.flowDiagram.tableToggle")}>
          <DataTable<Row>
            caption={t("common.flowDiagram.tableCaption", { label })}
            columns={columns}
            rows={rows}
            getRowId={(row) => row.id}
            density="compact"
          />
        </Disclosure>
      ) : null}
    </div>
  );
}
