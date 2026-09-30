import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardPaste, Eye, EyeOff, Pencil, Undo2, Variable } from "lucide-react";
import { useMemo, useState } from "react";

import { request } from "../../../api/client";
import { appEnvQuery, appKeys, appQuery } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { announce } from "../../../app/Announcer";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { QueryState } from "../../../components/page/QueryState";
import { SaveBar } from "../../../components/page/SaveBar";
import { appStatus } from "../../../components/page/status";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { CopyButton } from "../../../components/ui/CopyButton";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Menu, MenuGroup, MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { reportActionError } from "../../apps/useAppActions";
import type { DraftOp, EnvDiff, EnvRow } from "./draft";
import { applyDraft, diffEnv, draftRows, isMasked, summarise } from "./draft";
import { isValidName } from "./dotenv";
import { PasteDialog } from "./PasteDialog";
import { ReviewDialog } from "./ReviewDialog";
import { markFor, secrecyActionLabel, secrecyChoice, secrecyKind, secrecyLine, secrecyMarkAnnouncement } from "./secrecy";
import type { EnvSecrecy, SecrecyChoice } from "./secrecy";
import { VariableDialog } from "./VariableDialog";
import type { VariableTarget } from "./VariableDialog";

/** Where the app's `.env` lives: beside the code in a single folder, in `shared/` with instant rollback. */
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
      <span aria-hidden="true" className="text-13 leading-none tracking-widest">
        ••••••••
      </span>
      <span className="sr-only">{t("environment.tab.hidden")}</span>
    </span>
  );
}

const SECRECY_CHOICES: readonly SecrecyChoice[] = ["secret", "not-secret", "auto"];

/**
 * A variable's type: secret (hidden by the server) or plain, and in a few words what decided
 * it; the whole reason is the cell's title. A variable only in the draft is not classified yet.
 */
function TypeCell({ row, verdict, t }: { row: EnvRow; verdict: EnvSecrecy | undefined; t: T }) {
  if (row.current === null) return <span className="text-13 text-fg-muted">{t("environment.tab.notYetClassified")}</span>;
  if (verdict === undefined) return null;
  const kind = secrecyKind(verdict, t.locale);
  const Lock = ICONS.locked;
  return (
    <span title={secrecyLine(verdict, t.locale)} className={cx("inline-flex max-w-full min-w-0 items-center gap-1.5 align-middle text-13", row.state === "removed" && "line-through")}>
      {kind.secret ? <Lock aria-hidden="true" className="size-icon-sm shrink-0 text-fg-muted" /> : null}
      <span className="text-fg">{kind.label}</span>
      {/* Read as two words, drawn apart by the gap: a space the flex layout does not draw. */}
      {kind.reason !== null ? " " : null}
      {kind.reason !== null ? <span className="truncate text-fg-muted">{t("environment.tab.typeReason", { reason: kind.reason })}</span> : null}
    </span>
  );
}

/**
 * The variables in an app's `.env`: plain values in view, secrets hidden until revealed one at
 * a time (behind "Confirm it's you"), changed as a draft (row by row, or by pasting a whole
 * file) that the save bar at the foot reviews and writes, over the values the file really holds.
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

  /** Whether the value is on screen without asking: a plain value, or one the draft holds. */
  const inView = (row: EnvRow): boolean => row.draft !== null || (row.current !== null && !isMasked(row.current)) || revealed.has(row.name);

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
      width: "w-2/5",
      cell: (row) => (
        <span className="flex min-w-0 items-center gap-2">
          <Mono truncate title={row.name} tone={row.state === "removed" ? "muted" : "default"} className={cx(row.state === "removed" && "line-through")}>
            {row.name === "" ? t("environment.noName") : row.name}
          </Mono>
          {row.state !== "unchanged" ? <Badge>{stateBadge(t, row.state)}</Badge> : null}
          {row.state !== "removed" && !isValidName(row.name) ? <Badge tone="fail">{t("environment.tab.notValidName")}</Badge> : null}
        </span>
      ),
    },
    {
      id: "value",
      header: t("environment.tab.valueHeader"),
      cell: (row) => {
        const shown = inView(row);
        const value = shown ? valueOf(row) : null;
        const hideable = row.state !== "removed" && row.current !== null && isMasked(row.current) && row.draft === null;
        const reveal = hideable ? (
          <IconButton
            size="sm"
            label={revealed.has(row.name) ? t("environment.tab.hideAria", { name: row.name }) : t("environment.tab.revealAria", { name: row.name })}
            icon={revealed.has(row.name) ? <EyeOff /> : <Eye />}
            disabled={unmask.isPending}
            onClick={() => void toggleReveal(row)}
          />
        ) : null;
        if (value === null) {
          return (
            <span className="inline-flex max-w-full min-w-0 items-center gap-1 align-middle">
              <Masked />
              {reveal}
            </span>
          );
        }
        if (value === "") {
          return (
            <span className="inline-flex max-w-full min-w-0 items-center gap-1 align-middle">
              <span className="text-13 text-fg-faint">{t("environment.empty")}</span>
              {reveal}
            </span>
          );
        }
        return (
          <span className="inline-flex max-w-full min-w-0 items-center gap-1 align-middle">
            <Mono truncate title={value} tone={row.state === "removed" ? "muted" : "default"} className={cx("max-w-72", row.state === "removed" && "line-through")}>
              {value}
            </Mono>
            {reveal}
            <CopyButton value={value} label={t("environment.tab.copyValueAria", { name: row.name })} />
          </span>
        );
      },
    },
    {
      id: "type",
      header: t("environment.tab.typeHeader"),
      width: "w-64",
      cell: (row) => <TypeCell row={row} verdict={secrets.get(row.name)} t={t} />,
    },
  ];

  const rowActions = (row: EnvRow) => {
    const verdict = row.current !== null && row.state !== "removed" ? secrets.get(row.name) : undefined;
    const choice = verdict !== undefined ? secrecyChoice(verdict) : null;
    return (
      <Menu align="end" trigger={<IconButton size="sm" label={t("environment.tab.actionsAria", { name: row.name })} icon={<ICONS.more />} tooltip={false} />}>
        {row.state !== "removed" ? (
          <MenuItem icon={<Pencil />} disabled={unmask.isPending} onClick={() => void edit(row)}>
            {t("environment.tab.edit")}
          </MenuItem>
        ) : null}
        {row.state === "unchanged" ? (
          <MenuItem
            icon={<ICONS.delete />}
            onClick={() => {
              stage([{ kind: "remove", name: row.name }], t("environment.tab.willBeRemoved", { name: row.name }));
            }}
          >
            {t("environment.tab.remove")}
          </MenuItem>
        ) : (
          <MenuItem icon={<Undo2 />} onClick={() => undo(row.name)}>
            {t("environment.tab.undo")}
          </MenuItem>
        )}
        {choice !== null ? (
          <>
            <MenuSeparator />
            <MenuGroup label={t("environment.secrecy.groupLabel")}>
              {SECRECY_CHOICES.map((option) => (
                <MenuItem
                  key={option}
                  disabled={option === choice || (mark.isPending && mark.variables.name === row.name)}
                  onClick={() => {
                    mark.mutate({ name: row.name, value: markFor(option) });
                  }}
                >
                  {secrecyActionLabel(option, t.locale)}
                  {option === choice ? " " : null}
                  {option === choice ? <span className="sr-only">{t("environment.secrecy.currentSrOnly")}</span> : null}
                </MenuItem>
              ))}
            </MenuGroup>
          </>
        ) : null}
      </Menu>
    );
  };

  const actions = (
    <>
      <Button icon={<ClipboardPaste aria-hidden="true" />} onClick={() => setPasting(true)}>
        {t("environment.tab.pasteButton")}
      </Button>
      <Button icon={<ICONS.add aria-hidden="true" />} onClick={() => setEditing({ mode: "add" })}>
        {t("environment.addVariable")}
      </Button>
    </>
  );

  const empty = env.data !== undefined && rows.length === 0;

  return (
    <div className="flex flex-col gap-4">
      {empty ? null : (
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{isStatic ? t("environment.tab.introStatic") : t("environment.tab.introRuntime")}</p>
          <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
        </div>
      )}
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
            mobile="cards"
            skeletonRows={8}
            loading
          />
        }
        isEmpty={() => rows.length === 0}
        empty={
          <EmptyState
            variant="firstUse"
            level={2}
            icon={<Variable />}
            title={t("environment.tab.emptyTitle")}
            description={isStatic ? t("environment.tab.emptyDescriptionStatic") : t("environment.tab.emptyDescriptionRuntime")}
            action={actions}
            command={`noust env show ${domain}`}
          />
        }
      >
        {() => (
          <DataTable
            caption={t("environment.tab.tableCaption", { domain })}
            columns={columns}
            rows={rows}
            getRowId={(row) => row.name}
            rowActions={rowActions}
            density="compact"
            mobile="cards"
          />
        )}
      </QueryState>
      {empty ? null : (
        <>
          <SaveBar
            changes={counts.total}
            onDiscard={() => {
              setOps([]);
              announce(t("environment.tab.discardedAnnounce"));
            }}
            onSave={() => void openReview()}
            saving={unmask.isPending && review === null && counts.total > 0}
            saveLabel={t("environment.tab.reviewAndSave")}
          />
          {/* The empty state carries the same command; said once. */}
          <div className="flex flex-col gap-2">
            <p className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-12 text-fg-muted">
              {t.rich("environment.tab.storedIn", { file: <Mono tone="muted">{file}</Mono> })}
              <CopyButton value={file} label={t("environment.tab.copyPath")} />
            </p>
            <CommandHint command={`noust env show ${domain}`} label={t("environment.fromTerminal")} />
          </div>
        </>
      )}

      <VariableDialog
        target={editing}
        existing={names}
        onClose={() => setEditing(null)}
        onSubmit={(name, value) => {
          const adding = editing?.mode === "add";
          setEditing(null);
          stage([{ kind: "set", name, value }], adding ? t("environment.tab.willBeAdded", { name }) : t("environment.tab.willChange", { name }));
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
