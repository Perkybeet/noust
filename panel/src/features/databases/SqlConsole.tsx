import { Play } from "lucide-react";
import { useState } from "react";
import type { KeyboardEvent } from "react";

import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button } from "../../components/ui/Button";
import { Textarea } from "../../components/ui/Textarea";
import { useT } from "../../i18n";
import { engineLabel, supportsReadMode } from "./data";
import { ResultGrid } from "./ResultGrid";
import type { QueryResult } from "./ResultGrid";
import { useDatabaseActions } from "./useDatabaseActions";

/**
 * A mono editor that runs one statement at a time (databases.py's `/query` endpoint refuses an
 * embedded `;`), read-only by default where the engine's grammar supports it, write mode
 * always asking the API client's "Confirm it's you" dialog for a fresh elevation. The result -
 * columns, rows, row count and duration - is exactly what `execute_query_structured` timed and
 * parsed server-side; the console does no parsing of its own.
 */
export function SqlConsole({ engine, database }: { engine: string; database: string }) {
  const t = useT();
  const readAllowed = supportsReadMode(engine);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"read" | "write">(readAllowed ? "read" : "write");
  const [result, setResult] = useState<QueryResult | null>(null);
  const { runQuery } = useDatabaseActions();

  const run = (): void => {
    const statement = query.trim();
    if (statement === "" || runQuery.isPending) return;
    runQuery.mutate(
      { engine, database, query: statement, mode: readAllowed ? mode : "write" },
      {
        onSuccess: (response) => {
          setResult({
            columns: response.columns ?? [],
            rows: response.rows ?? [],
            rowCount: response.row_count,
            durationMs: response.duration_ms,
            truncated: response.truncated,
            output: response.output,
          });
        },
      },
    );
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      run();
    }
  };

  return (
    <Section title={t("databases.sqlConsole.title")} description={t("databases.sqlConsole.description")}>
      <div className="flex flex-col gap-3 rounded-card border border-border bg-surface p-4 shadow-raised">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {readAllowed ? (
            <SegmentedControl
              label={t("databases.sqlConsole.modeLabel")}
              value={mode}
              onValueChange={setMode}
              options={[
                { value: "read", label: t("databases.sqlConsole.readMode") },
                { value: "write", label: t("databases.sqlConsole.writeMode") },
              ]}
            />
          ) : (
            <p className="text-12 text-fg-faint">{t("databases.sqlConsole.noReadOnlyGrammar", { engine: engineLabel(engine) })}</p>
          )}
        </div>
        <Textarea
          mono
          rows={6}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
          }}
          onKeyDown={onKeyDown}
          placeholder={engine === "redis" ? "GET session:abc123" : "SELECT * FROM ..."}
          spellCheck={false}
          autoCapitalize="off"
        />
        <div className="flex items-center justify-between gap-3">
          <p className="text-12 text-fg-faint">{t("databases.sqlConsole.ctrlEnterHint")}</p>
          <Button variant="primary" icon={<Play aria-hidden="true" />} loading={runQuery.isPending} disabled={query.trim() === ""} onClick={run}>
            {t("databases.sqlConsole.run")}
          </Button>
        </div>
        {runQuery.isError ? <ErrorBlock live error={runQuery.error} title={t("databases.sqlConsole.statementFailed")} /> : null}
        {result !== null ? <ResultGrid result={result} /> : null}
      </div>
    </Section>
  );
}
