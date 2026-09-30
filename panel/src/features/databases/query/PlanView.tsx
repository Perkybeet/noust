import type { ExplainResult } from "../../../api/queries/databases";
import { Card } from "../../../components/ui/Card";
import { Mono } from "../../../components/ui/Mono";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatCount, formatDecimal } from "../../../lib/format";

/** One node of PostgreSQL's FORMAT JSON plan, as far as the tree needs it. */
interface PlanNode {
  "Node Type"?: string;
  "Relation Name"?: string;
  "Index Name"?: string;
  "Total Cost"?: number;
  "Plan Rows"?: number;
  "Actual Total Time"?: number;
  "Actual Rows"?: number;
  Plans?: PlanNode[];
}

function isNode(value: unknown): value is PlanNode {
  return typeof value === "object" && value !== null && "Node Type" in value;
}

/** The root of a PostgreSQL plan (`[{"Plan": {...}}]`), or null for any other shape. */
export function planRoot(plan: unknown): PlanNode | null {
  const first: unknown = Array.isArray(plan) ? plan[0] : plan;
  if (typeof first !== "object" || first === null) return null;
  const node: unknown = (first as Record<string, unknown>)["Plan"];
  return isNode(node) ? node : null;
}

function NodeLine({ node, depth, t }: { node: PlanNode; depth: number; t: T }) {
  const target = node["Relation Name"] ?? node["Index Name"];
  return (
    <>
      <li className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5 py-1 text-13">
        <span aria-hidden="true" className="shrink-0">
          {"  ".repeat(depth)}
        </span>
        <span className="font-medium text-fg">{node["Node Type"]}</span>
        {target !== undefined ? <Mono tone="muted">{target}</Mono> : null}
        <span className="text-12 text-fg-muted">
          {node["Total Cost"] !== undefined ? t("databases.plan.cost", { cost: formatDecimal(node["Total Cost"], t.locale) }) : null}
          {node["Plan Rows"] !== undefined ? ` · ${t("databases.plan.rows", { rows: formatCount(node["Plan Rows"], t.locale) })}` : null}
          {node["Actual Total Time"] !== undefined ? ` · ${t("databases.plan.time", { ms: formatDecimal(node["Actual Total Time"], t.locale) })}` : null}
        </span>
      </li>
      {(node.Plans ?? []).map((child, index) => (
        <NodeLine key={index} node={child} depth={depth + 1} t={t} />
      ))}
    </>
  );
}

/**
 * How the engine would run a statement: PostgreSQL's plan as a tree of steps with their cost
 * and estimated rows (and real times when it was analyzed), and in every case the plan as the
 * engine printed it, verbatim.
 */
export function PlanView({ result }: { result: ExplainResult }) {
  const t = useT();
  const root = result.format === "json" ? planRoot(result.plan) : null;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {root !== null ? (
        <Card padding="none" as="div">
          <ol aria-label={t("databases.plan.tree")} className="flex min-w-0 flex-col divide-y divide-border overflow-x-auto px-4 py-1 whitespace-pre scroll-thin">
            <NodeLine node={root} depth={0} t={t} />
          </ol>
        </Card>
      ) : null}
      <SystemOutput label={t("databases.plan.text")} maxHeight="max-h-96">
        {result.text}
      </SystemOutput>
    </div>
  );
}
