import { useMutation, useQuery } from "@tanstack/react-query";
import { Search, ShieldQuestion } from "lucide-react";

import { isApiError, request } from "../../../api/client";
import { appSqliteQuery } from "../../../api/queries/databases";
import type { SqliteCheck, SqliteFile } from "../../../api/queries/databases";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { reportActionError } from "../../apps/useAppActions";

/** What a check said, in the words of the console and, for a problem, SQLite's own. */
function CheckResult({ result, t }: { result: SqliteCheck; t: T }) {
  if (!result.ok) {
    return (
      <Notice live tone="error" title={t("databases.appTab.sqlite.checkProblemsTitle", { path: result.path })}>
        <div className="flex flex-col gap-2">
          <p>{t("databases.appTab.sqlite.checkProblemsBody")}</p>
          <SystemOutput label={t("databases.appTab.sqlite.checkOutput", { path: result.path })}>{result.problems.join("\n")}</SystemOutput>
        </div>
      </Notice>
    );
  }
  if (!result.complete) {
    return (
      <InlineMessage live>
        {t("databases.appTab.sqlite.checkPartialTitle")}. {t("databases.appTab.sqlite.checkPartialBody")}
      </InlineMessage>
    );
  }
  return (
    <InlineMessage live tone="success">
      {t("databases.appTab.sqlite.checkOk", { seconds: String(result.seconds) })}. {t("databases.appTab.sqlite.checkOkBody", { path: result.path })}
    </InlineMessage>
  );
}

/** One SQLite file: what its header says, and a check SQLite runs without closing it. */
function FileCard({ domain, file, t }: { domain: string; file: SqliteFile; t: T }) {
  const check = useMutation({
    mutationFn: (full: boolean) => request("post", "/api/apps/{domain}/sqlite/check", { params: { domain }, body: { path: file.path, full } }),
    onError: (error) => reportActionError(t("databases.appTab.sqlite.checkFailed", { path: file.path }), error),
  });
  const items = [
    { label: t("databases.appTab.sqlite.size"), value: formatBytes(file.size, t.locale), copy: false as const, mono: false },
    {
      label: t("databases.appTab.sqlite.journal"),
      value: file.journal_mode === "wal" ? t("databases.appTab.sqlite.journalWal") : t("databases.appTab.sqlite.journalRollback"),
      copy: false as const,
      mono: false,
    },
    ...(file.wal_bytes > 0 ? [{ label: t("databases.appTab.sqlite.waiting"), value: formatBytes(file.wal_bytes, t.locale), copy: false as const, mono: false }] : []),
    { label: t("databases.appTab.sqlite.changed"), value: <RelativeTime value={file.modified} />, copy: false as const, mono: false },
  ];
  return (
    <Card
      padding="sm"
      as="li"
      level={3}
      title={<Mono className="break-all">{file.path}</Mono>}
      actions={
        <>
          <Button icon={<ShieldQuestion aria-hidden="true" />} size="sm" variant="ghost" loading={check.isPending && !check.variables} disabled={check.isPending} onClick={() => check.mutate(false)}>
            {t("databases.appTab.sqlite.check")}
          </Button>
          <Button icon={<Search aria-hidden="true" />} size="sm" variant="ghost" loading={check.isPending && check.variables} disabled={check.isPending} onClick={() => check.mutate(true)}>
            {t("databases.appTab.sqlite.checkFull")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <KeyValueList items={items} />
        {check.isPending ? (
          <p role="status" className="text-13 text-fg-muted">
            {t("databases.appTab.sqlite.checkRunning", { path: file.path })}
          </p>
        ) : null}
        {check.data !== undefined ? <CheckResult result={check.data} t={t} /> : null}
      </div>
    </Card>
  );
}

/**
 * The SQLite databases an application keeps in its own data (a Vaultwarden, a Memos, a Django
 * on its default), under its Database tab: each file with what its header says, and a check that
 * asks SQLite whether it is sound without closing it. Shown only when the application has some;
 * an application backup already copies each one whole, which the section says once.
 */
export function SqliteSection({ domain }: { domain: string }) {
  const t = useT();
  const found = useQuery(appSqliteQuery(domain));
  // A server that does not have the endpoint yet (an older node behind a central) answers 404 or 405.
  if (found.isError && isApiError(found.error) && (found.error.status === 404 || found.error.status === 405)) return null;
  // design-exception: hand-error-ternary an application without SQLite files shows nothing at all, so there is no skeleton to stand in for it
  if (found.isError && found.data === undefined) {
    return <ErrorBlock error={found.error} title={t("databases.appTab.sqlite.loadFailed")} onRetry={() => void found.refetch()} retrying={found.isRefetching} />;
  }
  const data = found.data;
  if (data === undefined || data.files.length === 0) return null;
  return (
    <Section icon={OBJECT_ICONS.database} title={t("databases.appTab.sqlite.title")} description={t("databases.appTab.sqlite.intro")}>
      {data.truncated ? <InlineMessage tone="info">{t("databases.appTab.sqlite.truncated")}</InlineMessage> : null}
      <ul className="grid gap-4 lg:grid-cols-2">
        {data.files.map((file) => (
          <FileCard key={file.path} domain={domain} file={file} t={t} />
        ))}
      </ul>
      <CommandHint command={`noust db sqlite list ${domain}`} label={t("databases.common.fromTerminal")} />
    </Section>
  );
}
