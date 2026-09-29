import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardPaste, Eye, EyeOff, Lock, Pencil, Plus, ShieldQuestion, Trash2, Undo2, Variable } from "lucide-react";
import { useMemo, useState } from "react";

import { request } from "../../../api/client";
import { appEnvQuery, appKeys, appQuery } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { announce } from "../../../app/Announcer";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { QueryState } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { appStatus } from "../../../components/page/status";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { CopyButton } from "../../../components/ui/CopyButton";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { IconButton } from "../../../components/ui/IconButton";
import { Menu, MenuItem } from "../../../components/ui/Menu";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { reportActionError } from "../../apps/useAppActions";
import type { DraftOp, EnvDiff, EnvRow } from "./draft";
import { applyDraft, describeCounts, diffEnv, draftRows, isMasked, summarise } from "./draft";
import { isValidName } from "./dotenv";
import { PasteDialog } from "./PasteDialog";
import { ReviewDialog } from "./ReviewDialog";
import { markFor, secrecyActionLabel, secrecyChoice, secrecyLine, secrecyMarkAnnouncement, secrecyStateLabel } from "./secrecy";
import type { EnvSecrecy, SecrecyChoice } from "./secrecy";
import { VariableDialog } from "./VariableDialog";
import type { VariableTarget } from "./VariableDialog";

/** Where the app's `.env` lives: beside the code in place, in `shared/` on releases. */
function envFile(app: App | undefined): string {
  if (app?.path === undefined || app.path === null) return ".env";
  return app.layout === "releases" ? `${app.path}/shared/.env` : `${app.path}/.env`;
}

function stateBadge(t: T, state: Exclude<EnvRow["state"], "unchanged">): string {
  if (state === "added") return t("environment.added");
  if (state === "changed") return t("environment.changed");
  return t("environment.removed");
}

function Masked() {
  const t = useT();
  return (
    <span className="text-fg-faint">
      <span aria-hidden="true" className="text-13 leading-none tracking-[0.08em]">
        ••••••••
      </span>
      <span className="sr-only">{t("environment.tab.hidden")}</span>
    </span>
  );
}

const SECRECY_CHOICES: readonly SecrecyChoice[] = ["secret", "not-secret", "auto"];

/**
 * The control behind "an accessible control per variable to mark it secret / not secret /
 * automatic": a menu, so its three text-labelled options are reachable and operable from the
 * keyboard, and the current choice is named rather than only coloured. A choice writes through
 * PUT /env/marks immediately - it is not part of the draft, which only ever edits values.
 */
function SecrecyMenu({
  name,
  verdict,
  pending,
  onMark,
}: {
  name: string;
  verdict: EnvSecrecy;
  pending: boolean;
  onMark: (mark: boolean | null) => void;
}) {
  const t = useT();
  const choice = secrecyChoice(verdict);
  return (
    <Menu
      trigger={
        <IconButton
          size="sm"
          label={t("environment.secrecy.changeAria", { name, state: secrecyStateLabel(choice, t.locale) })}
          icon={<ShieldQuestion />}
          disabled={pending}
        />
      }
    >
      {SECRECY_CHOICES.map((option) => (
        <MenuItem
          key={option}
          disabled={option === choice}
          onClick={() => {
            onMark(markFor(option));
          }}
        >
          {secrecyActionLabel(option, t.locale)}
          {option === choice ? <span className="sr-only">{t("environment.secrecy.currentSrOnly")}</span> : null}
        </MenuItem>
      ))}
    </Menu>
  );
}

/**
 * The variables in an app's `.env`: masked until revealed one at a time, changed as a draft
 * (row by row, or by pasting a whole file), and written only after the change is reviewed
 * over the values the file really holds.
 */
export function EnvironmentTab({ domain }: { domain: string }) {
  const t = useT();
  useDocumentTitle(t("environment.tab.documentTitle", { domain }), 1);
  const queryClient = useQueryClient();
  const app = useQuery(appQuery(domain));
  const env = useQuery(appEnvQuery(domain, false));

  const [ops, setOps] = useState<DraftOp[]>([]);
  const [revealed, setRevealed] = useState<ReadonlySet<string>>(new Set());
  // Values in clear live here, in this page only: never in the shared query cache.
  const [clear, setClear] = useState<ReadonlyMap<string, string> | null>(null);
  const [editing, setEditing] = useState<VariableTarget | null>(null);
  const [pasting, setPasting] = useState(false);
  const [review, setReview] = useState<EnvDiff | null>(null);

  const unmask = useMutation({
    mutationFn: () => request("get", "/api/apps/{domain}/env", { params: { domain }, query: { unmask: true } }),
    onSuccess: (result) => {
      setClear(new Map(Object.entries(result.variables)));
    },
  });

  // Marking a variable is not part of the draft: it changes how Noust classifies the name, not
  // what the file holds, so it writes through immediately and refreshes the listing.
  const mark = useMutation({
    mutationFn: ({ name, value }: { name: string; value: boolean | null }) =>
      request("put", "/api/apps/{domain}/env/marks", { params: { domain }, body: { marks: { [name]: value } } }),
    onSuccess: (_result, variables) => {
      void queryClient.invalidateQueries({ queryKey: appKeys.env(domain, false) });
      announce(secrecyMarkAnnouncement(variables.name, variables.value));
    },
    onError: (error: unknown) => {
      reportActionError(t("environment.secrecy.markFailed"), error);
    },
  });

  const masked = useMemo(() => new Map(Object.entries(env.data?.variables ?? {})), [env.data]);
  const secrets = useMemo(() => new Map(Object.entries(env.data?.secrets ?? {})), [env.data]);
  const rows = useMemo(() => draftRows(masked, ops, clear), [masked, ops, clear]);
  const counts = summarise(rows);
  const names = useMemo(() => new Set(rows.filter((row) => row.state !== "removed").map((row) => row.name)), [rows]);
  const file = envFile(app.data);
  const isStatic = appStatus(app.data?.status).state === "static";

  /** The values in clear, read once per visit, asking the operator to confirm it's them. */
  const readClear = async (fresh = false): Promise<ReadonlyMap<string, string> | null> => {
    if (clear !== null && !fresh) return clear;
    try {
      const result = await unmask.mutateAsync();
      return new Map(Object.entries(result.variables));
    } catch (error: unknown) {
      reportActionError(t("environment.tab.readClearFailed"), error);
      return null;
    }
  };

  const valueOf = (row: EnvRow): string | null => {
    if (row.draft !== null) return row.draft;
    if (row.current !== null && !isMasked(row.current)) return row.current;
    return clear?.get(row.name) ?? null;
  };

  const toggleReveal = async (row: EnvRow): Promise<void> => {
    if (revealed.has(row.name)) {
      setRevealed((current) => {
        const next = new Set(current);
        next.delete(row.name);
        return next;
      });
      return;
    }
    if (valueOf(row) === null && (await readClear()) === null) return;
    setRevealed((current) => new Set(current).add(row.name));
  };

  const edit = async (row: EnvRow): Promise<void> => {
    let value = valueOf(row);
    if (value === null) {
      const values = await readClear();
      if (values === null) return;
      value = values.get(row.name) ?? "";
    }
    setEditing({ mode: "edit", name: row.name, value });
  };

  const stage = (next: DraftOp[], message: string): void => {
    setOps((current) => [...current, ...next]);
    announce(message);
  };

  const undo = (name: string): void => {
    setOps((current) => [...current.filter((op) => !("name" in op && op.name === name)), { kind: "restore", name }]);
    announce(t("environment.tab.undidChange", { name }));
  };

  const openReview = async (): Promise<void> => {
    // Read fresh: the review compares against what the file holds now, not when the page opened.
    const values = await readClear(true);
    if (values === null) return;
    setReview(diffEnv(values, applyDraft(values, ops)));
  };

  const columns: Column<EnvRow>[] = [
    {
      id: "name",
      header: t("environment.tab.nameHeader"),
      width: "w-[38%]",
      cell: (row) => (
        <span className="flex min-w-0 items-center gap-2">
          <span
            translate="no"
            title={row.name}
            className={cx("mono max-w-[9rem] truncate text-12 sm:max-w-[20rem]", row.state === "removed" ? "text-fg-muted line-through" : "text-fg")}
          >
            {row.name === "" ? t("environment.noName") : row.name}
          </span>
          {row.current !== null && isMasked(row.current) && row.state !== "added" ? (
            <span className="inline-flex text-fg-faint" title={t("environment.tab.hiddenByServerTitle")}>
              <Lock aria-hidden="true" className="size-3.5" />
              <span className="sr-only">{t("environment.tab.secretSrOnly")}</span>
            </span>
          ) : null}
          {row.state !== "unchanged" ? <Badge>{stateBadge(t, row.state)}</Badge> : null}
          {row.state !== "removed" && !isValidName(row.name) ? <Badge tone="fail">{t("environment.tab.notValidName")}</Badge> : null}
        </span>
      ),
    },
    {
      id: "value",
      header: t("environment.tab.valueHeader"),
      cell: (row) => {
        const value = revealed.has(row.name) ? valueOf(row) : null;
        if (value === null) return <Masked />;
        if (value === "") return <span className="text-13 text-fg-faint">{t("environment.empty")}</span>;
        return (
          <span className="flex min-w-0 items-center gap-1">
            <span
              translate="no"
              title={value}
              className={cx("mono max-w-[8rem] truncate text-12 sm:max-w-[26rem]", row.state === "removed" ? "text-fg-muted line-through" : "text-fg")}
            >
              {value}
            </span>
            <CopyButton value={value} label={t("environment.tab.copyValueAria", { name: row.name })} />
          </span>
        );
      },
    },
    {
      id: "visibility",
      header: t("environment.tab.visibilityHeader"),
      width: "w-56",
      cell: (row) => {
        // A variable just added exists only in the draft: Noust has not classified it yet, and
        // marking it before it is even saved would set an override for a name the .env file
        // does not hold. A removed one keeps its line, struck through, but not the control.
        if (row.current === null) return <span className="text-13 text-fg-faint">{t("environment.tab.notYetClassified")}</span>;
        const verdict = secrets.get(row.name);
        if (verdict === undefined) return null;
        const line = secrecyLine(verdict, t.locale);
        return (
          <span className="flex min-w-0 items-center gap-2">
            <span
              title={line}
              className={cx("min-w-0 max-w-[13rem] truncate text-13", row.state === "removed" ? "text-fg-muted line-through" : "text-fg-muted")}
            >
              {line}
            </span>
            {row.state !== "removed" ? (
              <SecrecyMenu
                name={row.name}
                verdict={verdict}
                pending={mark.isPending && mark.variables.name === row.name}
                onMark={(value) => {
                  mark.mutate({ name: row.name, value });
                }}
              />
            ) : null}
          </span>
        );
      },
    },
  ];

  const rowActions = (row: EnvRow) => {
    const shown = revealed.has(row.name);
    return (
      <span className="inline-flex items-center justify-end gap-0.5">
        {row.state !== "removed" ? (
          <>
            <IconButton
              size="sm"
              label={shown ? t("environment.tab.hideAria", { name: row.name }) : t("environment.tab.revealAria", { name: row.name })}
              icon={shown ? <EyeOff /> : <Eye />}
              disabled={unmask.isPending}
              onClick={() => void toggleReveal(row)}
            />
            <IconButton size="sm" label={t("environment.tab.editAria", { name: row.name })} icon={<Pencil />} disabled={unmask.isPending} onClick={() => void edit(row)} />
          </>
        ) : null}
        {row.state === "unchanged" ? (
          <IconButton
            size="sm"
            label={t("environment.tab.removeAria", { name: row.name })}
            icon={<Trash2 />}
            onClick={() => {
              stage([{ kind: "remove", name: row.name }], t("environment.tab.willBeRemoved", { name: row.name }));
            }}
          />
        ) : (
          <IconButton size="sm" label={t("environment.tab.undoAria", { name: row.name })} icon={<Undo2 />} onClick={() => undo(row.name)} />
        )}
      </span>
    );
  };

  const actions = (
    <>
      <Button icon={<ClipboardPaste aria-hidden="true" />} onClick={() => setPasting(true)}>
        {t("environment.tab.pasteButton")}
      </Button>
      <Button icon={<Plus aria-hidden="true" />} onClick={() => setEditing({ mode: "add" })}>
        {t("environment.addVariable")}
      </Button>
    </>
  );

  // An editor, capped like a form: the value column truncates long before a wide screen's
  // edge, and past that the row's own actions would drift away from the variable.
  return (
    <div className="flex max-w-6xl flex-col gap-8">
      <Section
        title={t("environment.tab.title")}
        // Two lines whatever the path's length: the sentence, then the file on a line of its
        // own, cut short with the whole path on hover. A path that wrapped the sentence moved
        // the table down when the app's details arrived.
        description={
          <>
            <span className="block">{isStatic ? t("environment.tab.readBuild") : t("environment.tab.readRuntime")}</span>
            <code translate="no" title={file} className="block truncate text-12 text-fg">
              {file}
            </code>
          </>
        }
        actions={env.data !== undefined && masked.size + counts.added > 0 ? actions : undefined}
      >
        <p className="text-13 text-fg-muted">{t("environment.tab.autoHideExplanation")}</p>
        <QueryState
          query={env}
          label={t("environment.tab.queryLabel")}
          skeleton={
            <DataTable
              caption={t("environment.tab.tableCaption", { domain })}
              columns={columns}
              rows={[]}
              getRowId={(row) => row.name}
              rowActions={rowActions}
              density="compact"
              loading
            />
          }
          isEmpty={() => rows.length === 0}
          empty={
            <EmptyState
              level={3}
              icon={<Variable />}
              title={t("environment.tab.emptyTitle")}
              description={isStatic ? t("environment.tab.emptyDescriptionStatic") : t("environment.tab.emptyDescriptionRuntime")}
              action={actions}
              command={`noust env show ${domain}`}
            />
          }
        >
          {() => (
            <div className="flex flex-col gap-3">
              {counts.total > 0 ? (
                <div className="flex flex-col gap-3 rounded-card border border-border bg-surface px-4 py-3 shadow-raised sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-13 text-fg">
                    <span className="font-medium">{t("environment.tab.unsavedChanges", { count: counts.total })}</span>
                    <span className="text-fg-muted">{`: ${t("environment.tab.unsavedDetail", { counts: describeCounts(counts, t.locale) })}`}</span>
                  </p>
                  <div className="flex shrink-0 items-center gap-2">
                    <Button
                      onClick={() => {
                        setOps([]);
                        announce(t("environment.tab.discardedAnnounce"));
                      }}
                    >
                      {t("environment.tab.discard")}
                    </Button>
                    <Button variant="primary" loading={unmask.isPending && review === null} onClick={() => void openReview()}>
                      {t("environment.tab.reviewAndSave")}
                    </Button>
                  </div>
                </div>
              ) : null}
              <DataTable
                caption={t("environment.tab.tableCaption", { domain })}
                columns={columns}
                rows={rows}
                getRowId={(row) => row.name}
                rowActions={rowActions}
                density="compact"
              />
            </div>
          )}
        </QueryState>
        {rows.length > 0 || env.data === undefined ? (
          <p className="flex items-center gap-1.5 text-12 text-fg-muted">
            <Lock aria-hidden="true" className="size-3.5 shrink-0 text-fg-faint" />
            {t("environment.tab.secretsHiddenNote")}
          </p>
        ) : null}
        {/* The empty state carries the same command; said once. */}
        {rows.length > 0 || env.data === undefined ? <CommandHint command={`noust env show ${domain}`} label={t("environment.fromTerminal")} /> : null}
      </Section>

      <VariableDialog
        target={editing}
        existing={names}
        onClose={() => setEditing(null)}
        onSubmit={(name, value) => {
          const adding = editing?.mode === "add";
          setEditing(null);
          stage([{ kind: "set", name, value }], adding ? t("environment.tab.willBeAdded", { name }) : t("environment.tab.willChange", { name }));
          if (adding) setRevealed((current) => new Set(current).add(name));
        }}
      />
      <PasteDialog open={pasting} onOpenChange={setPasting} current={masked} onStage={stage} />
      <ReviewDialog
        domain={domain}
        file={file}
        isStatic={isStatic}
        diff={review}
        onClose={() => setReview(null)}
        onSaved={(next) => {
          setOps([]);
          setClear(next);
          void queryClient.invalidateQueries({ queryKey: appKeys.env(domain, false) });
        }}
      />
    </div>
  );
}
