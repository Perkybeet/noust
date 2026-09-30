import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Download, History, Play, Save } from "lucide-react";
import { useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { databaseKeys, databaseOverviewQuery, enginesQuery } from "../../../api/queries/databases";
import type { ExplainResult, QueryResult } from "../../../api/queries/databases";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Drawer } from "../../../components/ui/Drawer";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Kbd } from "../../../components/ui/Kbd";
import { Menu, MenuItem } from "../../../components/ui/Menu";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Switch } from "../../../components/ui/Switch";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { Tab, TabList, TabPanel, Tabs } from "../../../components/ui/Tabs";
import { toast } from "../../../components/ui/toast";
import { useMediaQuery } from "../../../components/ui/useMediaQuery";
import { useT } from "../../../i18n";
import { downloadText } from "../../../lib/clipboard";
import { formatCount, formatDecimal } from "../../../lib/format";
import { reportActionError } from "../../apps/useAppActions";
import { can } from "../engines";
import { DataGrid } from "../grid/DataGrid";
import { PlanView } from "./PlanView";
import { QueryLibrary } from "./QueryLibrary";
import { SqlEditor } from "./SqlEditor";
import type { SqlEditorHandle } from "./SqlEditor";

type Timeout = 5 | 30 | 120;
const TIMEOUTS: readonly Timeout[] = [5, 30, 120];
const ROW_LIMITS = [100, 1000, 5000] as const;
const XL_UP = "(min-width: 1280px)";

type ResultTab = "results" | "plan" | "output";

/** The statement to run: the selection when there is one, the whole editor otherwise. */
function statementOf(editor: SqlEditorHandle | null, text: string): string {
  const selected = editor?.selection().trim() ?? "";
  return selected !== "" ? selected : text.trim();
}

function SaveDialog({ open, onOpenChange, engine, name, statement }: { open: boolean; onOpenChange: (open: boolean) => void; engine: string; name: string; statement: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [label, setLabel] = useState("");
  const [anyDatabase, setAnyDatabase] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => request("post", "/api/databases/console/saved", { body: { name: label.trim(), engine, database: anyDatabase ? null : name, query: statement } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.saved(engine, name) });
      toast.success(t("databases.query.saved", { name: label.trim() }));
      onOpenChange(false);
      setLabel("");
    },
  });
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (label.trim() === "") {
      setError(t("databases.query.saveNameRequired"));
      return;
    }
    setError(null);
    save.mutate();
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={t("databases.query.saveTitle")}
      description={t("databases.query.saveDescription")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("databases.common.cancel")}</Button>
          <Button type="submit" form="save-query-form" variant="primary" loading={save.isPending}>
            {t("databases.query.saveAction")}
          </Button>
        </>
      }
    >
      <form id="save-query-form" noValidate onSubmit={submit} className="flex flex-col gap-5">
        <Field label={t("databases.query.saveName")} error={error}>
          <Input value={label} onValueChange={(value: string) => setLabel(value)} maxLength={120} />
        </Field>
        <Checkbox checked={anyDatabase} onCheckedChange={setAnyDatabase} label={t("databases.query.anyDatabase")} description={t("databases.query.anyDatabaseHelp")} />
        <SystemOutput label={t("databases.query.statement")} maxHeight="max-h-40">
          {statement}
        </SystemOutput>
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("databases.query.saveFailed")} /> : null}
      </form>
    </Dialog>
  );
}

/**
 * The SQL console: an editor with line numbers and quiet highlighting, run with Ctrl+Enter
 * (the selection alone when there is one) under a timeout the server enforces and a row
 * limit; read-only by default, held so by the engine itself, with changing data an explicit
 * switch that asks "Confirm it's you" on the first write. Results as a grid, the plan as a tree
 * and the engine's own output verbatim; history and saved statements at the side; a read's
 * whole result exported as CSV or JSON.
 */
export function QueryTab({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const wide = useMediaQuery(XL_UP);
  const editor = useRef<SqlEditorHandle>(null);
  const overview = useQuery(databaseOverviewQuery(engine, name));
  const engines = useQuery(enginesQuery());
  const capabilities = overview.data?.capabilities ?? engines.data?.engines.find((item) => item.name === engine)?.capabilities;
  // Until the engine says otherwise it reads, as SQL engines do: no warning flashes in and out.
  const readable = capabilities === undefined || can({ capabilities }, "read_only");

  const [text, setText] = useState("SELECT 1;");
  const [write, setWrite] = useState(false);
  const [timeout, setTimeoutS] = useState<Timeout>(30);
  const [rowLimit, setRowLimit] = useState<number>(1000);
  const [tab, setTab] = useState<ResultTab>("results");
  const [saving, setSaving] = useState<string | null>(null);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const mode = write || !readable ? "write" : "read";

  const refreshHistory = (): void => void queryClient.invalidateQueries({ queryKey: databaseKeys.history(engine, name) });

  const run = useMutation({
    mutationFn: (statement: string): Promise<QueryResult> =>
      request("post", "/api/databases/query", { body: { engine, database: name, query: statement, mode, max_rows: 500, row_limit: rowLimit, timeout_s: timeout } }),
    onSuccess: (result) => {
      refreshHistory();
      setTab((result.columns ?? []).length > 0 ? "results" : "output");
      if (mode === "write") void queryClient.invalidateQueries({ queryKey: databaseKeys.database(engine, name) });
    },
    onError: () => {
      refreshHistory();
      setTab("results");
    },
  });

  const explain = useMutation({
    mutationFn: ({ statement, analyze }: { statement: string; analyze: boolean }): Promise<ExplainResult> =>
      request("post", "/api/databases/query/explain", { body: { engine, database: name, query: statement, analyze, timeout_s: timeout } }),
    onSuccess: () => {
      refreshHistory();
      setTab("plan");
    },
    onError: () => setTab("plan"),
  });

  const exporting = useMutation({
    mutationFn: async ({ statement, format }: { statement: string; format: "csv" | "json" }) => {
      const body = await request("post", "/api/databases/query/export", { body: { engine, database: name, query: statement, format, row_limit: 50_000, timeout_s: timeout } });
      const content = typeof body === "string" ? body : JSON.stringify(body, null, 2);
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      downloadText(`${name}-${stamp}.${format}`, content, format === "csv" ? "text/csv" : "application/json");
    },
    onError: (error) => reportActionError(t("databases.query.exportFailed"), error),
  });

  const execute = (): void => {
    const statement = statementOf(editor.current, text);
    if (statement !== "") run.mutate(statement);
  };
  const plan = (analyze: boolean): void => {
    const statement = statementOf(editor.current, text);
    if (statement !== "") explain.mutate({ statement, analyze });
  };

  const result = run.data;
  const lastStatement = run.variables ?? "";
  const busy = run.isPending || explain.isPending;
  const library = (
    <QueryLibrary
      engine={engine}
      name={name}
      onPick={(statement) => {
        setText(statement);
        setLibraryOpen(false);
        editor.current?.focus();
      }}
    />
  );

  const meta =
    result !== undefined ? (
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-12 text-fg-muted">
        <span>{t("databases.query.rowCount", { count: result.row_count, rows: formatCount(result.row_count, t.locale) })}</span>
        <span>{t("databases.query.duration", { ms: formatDecimal(result.duration_ms, t.locale) })}</span>
        <span>{result.mode === "write" ? t("databases.query.ranWrite") : t("databases.query.ranRead")}</span>
        {result.truncated ? <span className="text-fg">{t("databases.query.truncated", { limit: formatCount(rowLimit, t.locale) })}</span> : null}
      </span>
    ) : null;

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-6 xl:flex-row xl:items-start">
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          {mode === "write" ? (
            <Notice tone="warning" title={readable ? t("databases.query.writeOnTitle") : t("databases.query.writeOnlyTitle")}>
              {readable ? t("databases.query.writeOnBody") : t("databases.query.writeOnlyBody")}
            </Notice>
          ) : null}
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" icon={<Play aria-hidden="true" />} loading={run.isPending} onClick={execute}>
                {t("databases.query.run")}
                <Kbd className="ml-1">{t("databases.query.runKeys")}</Kbd>
              </Button>
              <Menu
                trigger={
                  <Button size="sm" trailingIcon={<ChevronDown aria-hidden="true" />} disabled={busy}>
                    {t("databases.query.explain")}
                  </Button>
                }
              >
                <MenuItem onClick={() => plan(false)}>{t("databases.query.explainPlan")}</MenuItem>
                <MenuItem onClick={() => plan(true)}>{t("databases.query.explainAnalyze")}</MenuItem>
              </Menu>
              <Button size="sm" variant="ghost" icon={<Save aria-hidden="true" />} onClick={() => setSaving(statementOf(editor.current, text))} disabled={text.trim() === ""}>
                {t("databases.query.save")}
              </Button>
              {!wide ? (
                <Button size="sm" variant="ghost" icon={<History aria-hidden="true" />} onClick={() => setLibraryOpen(true)}>
                  {t("databases.query.library")}
                </Button>
              ) : null}
            </div>
          </div>
          <SqlEditor ref={editor} value={text} onChange={setText} onRun={execute} label={t("databases.query.editorLabel", { name })} className="h-56 resize-y" />
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
              {readable ? <Switch checked={write} onCheckedChange={setWrite} label={t("databases.query.allowChanges")} /> : <span />}
              <div className="flex flex-wrap items-center gap-2">
              <Select
                size="sm"
                aria-label={t("databases.query.timeoutLabel")}
                value={String(timeout)}
                onValueChange={(value) => setTimeoutS(Number(value) as Timeout)}
                options={TIMEOUTS.map((seconds) => ({ value: String(seconds), label: t("databases.query.timeout", { seconds }) }))}
              />
              <Select
                size="sm"
                aria-label={t("databases.query.rowLimitLabel")}
                value={String(rowLimit)}
                onValueChange={(value) => setRowLimit(Number(value))}
                options={ROW_LIMITS.map((limit) => ({ value: String(limit), label: t("databases.query.rowLimit", { rows: formatCount(limit, t.locale) }) }))}
              />
            </div>
          </div>

          <Tabs value={tab} onValueChange={setTab} className="min-w-0 gap-3">
            <div className="flex min-w-0 flex-wrap items-end justify-between gap-x-4 gap-y-2">
              <TabList aria-label={t("databases.query.resultsLabel")}>
                <Tab value="results">{t("databases.query.results")}</Tab>
                <Tab value="plan">{t("databases.query.plan")}</Tab>
                <Tab value="output">{t("databases.query.output")}</Tab>
              </TabList>
              {result?.mode === "read" && (result.columns ?? []).length > 0 ? (
                <Menu
                  align="end"
                  trigger={
                    <Button size="sm" variant="ghost" icon={<Download aria-hidden="true" />} loading={exporting.isPending}>
                      {t("databases.query.export")}
                    </Button>
                  }
                >
                  <MenuItem onClick={() => exporting.mutate({ statement: lastStatement, format: "csv" })}>{t("databases.query.exportCsv")}</MenuItem>
                  <MenuItem onClick={() => exporting.mutate({ statement: lastStatement, format: "json" })}>{t("databases.query.exportJson")}</MenuItem>
                </Menu>
              ) : null}
            </div>
            <TabPanel value="results">
              {run.isError ? (
                <ErrorBlock live error={run.error} title={t("databases.query.failed")} />
              ) : result === undefined ? (
                <EmptyState variant="inline" title={run.isPending ? t("databases.query.running") : t("databases.query.nothingYet")} />
              ) : (result.columns ?? []).length === 0 ? (
                <div className="flex flex-col gap-2">
                  {meta}
                  <SystemOutput label={t("databases.query.outputLabel")} maxHeight="max-h-72">
                    {result.output || t("databases.query.noOutput")}
                  </SystemOutput>
                </div>
              ) : (
                <div className="flex min-w-0 flex-col gap-2">
                  {meta}
                  <DataGrid
                    label={t("databases.query.gridLabel")}
                    columns={(result.columns ?? []).map((column) => ({ name: column }))}
                    rows={(result.rows ?? []).map((cells, index) => ({ id: String(index), cells }))}
                    empty={<EmptyState variant="inline" title={t("databases.query.noRows")} />}
                  />
                </div>
              )}
            </TabPanel>
            <TabPanel value="plan">
              {explain.isError ? (
                <ErrorBlock live error={explain.error} title={t("databases.query.explainFailed")} />
              ) : explain.data === undefined ? (
                <EmptyState variant="inline" title={explain.isPending ? t("databases.query.explaining") : t("databases.query.noPlan")} />
              ) : (
                <PlanView result={explain.data} />
              )}
            </TabPanel>
            <TabPanel value="output">
              {result === undefined ? (
                <EmptyState variant="inline" title={t("databases.query.nothingYet")} />
              ) : (
                <SystemOutput label={t("databases.query.outputLabel")} maxHeight="max-h-96">
                  {result.output || t("databases.query.noOutput")}
                </SystemOutput>
              )}
            </TabPanel>
          </Tabs>
        </div>
        {wide ? <aside className="w-80 shrink-0">{library}</aside> : null}
      </div>
      <CommandHint command={`noust db query ${name} "SELECT 1" -e ${engine}`} label={t("databases.common.fromTerminal")} />
      {!wide ? (
        <Drawer open={libraryOpen} onOpenChange={setLibraryOpen} size="md" title={t("databases.query.library")}>
          {library}
        </Drawer>
      ) : null}
      <SaveDialog open={saving !== null} onOpenChange={(open) => (open ? undefined : setSaving(null))} engine={engine} name={name} statement={saving ?? ""} />
    </div>
  );
}
