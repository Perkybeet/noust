import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardPaste, Combine, Eye, EyeOff, Pencil, Play, Save, Variable, WandSparkles } from "lucide-react";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../../api/client";
import { appEnvQuery, appKeys, appQuery } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { AUDIENCES } from "../../../api/queries/variables";
import type { VariableAudience } from "../../../api/queries/variables";
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
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { reportActionError } from "../../apps/useAppActions";
import { audienceBody, audienceChanges, audienceHelp, audienceLabel, effectiveAudience, storedAudience } from "./audience";
import type { StagedAudience } from "./audience";
import type { DraftOp, EnvDiff, EnvRow } from "./draft";
import { draftRows, isMasked, mergeOf, reviewOf, summarise } from "./draft";
import { ComposeVariables } from "./ComposeVariables";
import { isValidName, oneLine } from "./dotenv";
import { PasteDialog } from "./PasteDialog";
import { PredefinedVariables } from "./PredefinedVariables";
import { ReviewDialog } from "./ReviewDialog";
import { markFor, secrecyActionLabel, secrecyChoice, secrecyKind, secrecyLine, secrecyMarkAnnouncement } from "./secrecy";
import type { EnvSecrecy, SecrecyChoice } from "./secrecy";
import { useSaveEnvironment } from "./useSaveEnvironment";
import { VariableDialog } from "./VariableDialog";
import type { VariableTarget } from "./VariableDialog";
import { OBJECT_ICONS, VERB_ICONS } from "../../../components/ui/objectIcons";

/** A change this large, or a pasted file, is looked over in full before it is written; anything smaller is already in the table. */
const REVIEW_ABOVE = 6;

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

/** What each way of deciding if a value is hidden looks like in the menu: hidden, shown, or left to Noust. */
const SECRECY_ICON: Record<SecrecyChoice, ReactNode> = { secret: <EyeOff />, "not-secret": <Eye />, auto: <WandSparkles /> };

/** Who gets a variable: both the build and the running app, the build only, the running app only. */
const AUDIENCE_ICON: Record<VariableAudience, ReactNode> = { both: <Combine />, build: <OBJECT_ICONS.build />, runtime: <Play /> };

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
 * file). A row opens on press; a change shows in the table, an old value struck beside the new
 * one; and the bar at the foot writes only what changed and restarts the app, or leaves the
 * change waiting as "not applied". Nothing is read in clear to change a variable: a hidden value
 * is kept or replaced. A large or pasted change is looked over in full before it is written.
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
  // A pasted file is looked over before it is written, however few variables it holds.
  const [pasted, setPasted] = useState(false);
  const save = useSaveEnvironment(domain);
  // Who gets which variable, chosen but not saved: written with the values, in one request.
  const [staged, setStaged] = useState<StagedAudience>(new Map());
  // The notes above variables, changed but not saved (empty text takes one away).
  const [stagedComments, setStagedComments] = useState<ReadonlyMap<string, string>>(new Map());

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

  // Reading every reference again writes the file (and asks the secret managers), so it is
  // sudo mode's; the running process keeps what it has until it restarts.
  const resolve = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/env/resolve", { params: { domain } }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: appKeys.env(domain, false) });
      setClear(null);
      announce(result.changed.length === 0 ? t("variables.env.resolveNothing") : t("variables.env.resolved", { count: result.changed.length }));
    },
    onError: (error: unknown) => {
      reportActionError(t("variables.env.resolveFailed"), error);
    },
  });

  const masked = useMemo(() => new Map(Object.entries(env.data?.variables ?? {})), [env.data]);
  const references = env.data?.references;
  const recordedAudience = env.data?.audience;
  const hasReferences = Object.keys(references ?? {}).length > 0;
  const secrets = useMemo(() => new Map(Object.entries(env.data?.secrets ?? {})), [env.data]);
  const rows = useMemo(() => draftRows(masked, ops, clear), [masked, ops, clear]);
  const counts = summarise(rows);
  const stagedChanges = audienceChanges(recordedAudience, staged, new Set(rows.filter((row) => row.state !== "removed").map((row) => row.name))).length;
  const names = useMemo(() => new Set(rows.filter((row) => row.state !== "removed").map((row) => row.name)), [rows]);
  const savedComments = env.data?.comments;
  /** The note above a variable as it will be: the staged one, else the saved one. */
  const commentOf = (name: string): string => stagedComments.get(name) ?? savedComments?.[name] ?? "";
  // A note that differs from the saved one, on a variable that stays: what the request carries.
  const commentChanges = new Map(
    [...stagedComments].filter(([name, text]) => names.has(name) && text !== (savedComments?.[name] ?? "")),
  );
  // A note on a variable the draft adds or changes is part of that row; the others count alone.
  const loneCommentChanges = [...commentChanges.keys()].filter((name) => rows.find((row) => row.name === name)?.state === "unchanged").length;
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

  /** What a reference reads as: the text the operator wrote, never what it resolved to. */
  const expressionOf = (row: EnvRow): string | null => (row.draft === null ? (references?.[row.name] ?? null) : null);

  const valueOf = (row: EnvRow): string | null => {
    if (row.draft !== null) return row.draft;
    const expression = expressionOf(row);
    if (expression !== null) return expression;
    if (row.current !== null && !isMasked(row.current)) return row.current;
    return clear?.get(row.name) ?? null;
  };

  /** Whether the value is on screen without asking: a plain value, or one the draft holds. */
  const inView = (row: EnvRow): boolean => row.draft !== null || expressionOf(row) !== null || (row.current !== null && !isMasked(row.current)) || revealed.has(row.name);

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

  /** A hidden value is not read to be edited: the dialog keeps it or replaces it (`value: null`). */
  const edit = (row: EnvRow): void => {
    setEditing({ mode: "edit", name: row.name, value: valueOf(row), audience: effectiveAudience(recordedAudience, staged, row.name), comment: commentOf(row.name) });
  };

  const stage = (next: DraftOp[], message: string, pasted?: ReadonlyMap<string, string>): void => {
    setOps((current) => [...current, ...next]);
    if (pasted !== undefined && pasted.size > 0) {
      setStagedComments((current) => new Map([...current, ...pasted]));
    }
    announce(message);
  };

  const undo = (name: string): void => {
    setOps((current) => [...current.filter((op) => !("name" in op && op.name === name)), { kind: "restore", name }]);
    setStaged((current) => {
      const next = new Map(current);
      next.delete(name);
      return next;
    });
    setStagedComments((current) => {
      const next = new Map(current);
      next.delete(name);
      return next;
    });
    announce(t("environment.tab.undidChange", { name }));
  };

  const stageAudience = (name: string, audience: VariableAudience): void => {
    setStaged((current) => new Map(current).set(name, audience));
    announce(t("variables.audience.staged", { name, audience: audienceLabel(audience, t.locale) }));
  };

  /** Who gets the variable, when it is not both or the operator is changing it. */
  const audienceBadge = (row: EnvRow) => {
    const now = effectiveAudience(recordedAudience, staged, row.name);
    const changing = now !== storedAudience(recordedAudience, row.name);
    if (now === "both" && !changing) return null;
    return <Badge tone={changing ? "accent" : "neutral"}>{changing ? t("variables.audience.badgeStaged", { audience: audienceLabel(now, t.locale) }) : audienceLabel(now, t.locale)}</Badge>;
  };

  const changeCount = counts.total + stagedChanges + loneCommentChanges;
  const merge = mergeOf(masked, ops);
  const audienceList = audienceChanges(recordedAudience, staged, names);
  const reviewComments = new Map([...commentChanges].filter(([name]) => names.has(name)));
  const needsReview = pasted || changeCount > REVIEW_ABOVE;

  const forget = (): void => {
    setOps([]);
    setStaged(new Map());
    setStagedComments(new Map());
    setPasted(false);
    // What was read in clear is the old file's now.
    setClear(null);
  };

  /** Writes only what changed; the app is restarted too when `restart`, else the change waits as "not applied". */
  const write = (restart: boolean): void => {
    save.mutate(
      {
        merge,
        ...(audienceList.length > 0 ? { audience: audienceBody(audienceList) } : {}),
        ...(reviewComments.size > 0 ? { comments: Object.fromEntries(reviewComments) } : {}),
        restart: restart && !isStatic,
      },
      {
        onSuccess: forget,
        onError: (error) => {
          reportActionError(t("environment.reviewDialog.saveFailed"), error);
        },
      },
    );
  };

  const saveAs = (restart: boolean): void => {
    if (needsReview) setReview(reviewOf(rows));
    else write(restart);
  };

  const columns: Column<EnvRow>[] = [
    {
      id: "name",
      header: t("environment.tab.nameHeader"),
      width: "w-2/5",
      cell: (row) => {
        const note = row.state === "removed" ? "" : commentOf(row.name);
        return (
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="flex min-w-0 items-center gap-2">
              <Mono truncate title={row.name} tone={row.state === "removed" ? "muted" : "default"} className={cx(row.state === "removed" && "line-through")}>
                {row.name === "" ? t("environment.noName") : row.name}
              </Mono>
              {/* What the draft will do: staged, so the accent (the violet marks what is chosen and not yet done). */}
              {row.state !== "unchanged" ? <Badge tone="accent">{stateBadge(t, row.state)}</Badge> : null}
              {row.state !== "removed" ? audienceBadge(row) : null}
              {row.state !== "removed" && !isValidName(row.name) ? <StatusPill state="failed" label={t("environment.tab.notValidName")} appearance="inline" size="sm" /> : null}
            </span>
            {note !== "" ? (
              <span title={note} className="truncate text-12 text-fg-muted">
                {oneLine(note).text}
              </span>
            ) : null}
          </span>
        );
      },
    },
    {
      id: "value",
      header: t("environment.tab.valueHeader"),
      cell: (row) => {
        const hiddenValue = row.current !== null && isMasked(row.current);
        // A changed variable shows what it was and what it will be, in the row: no dialog to open to see the change.
        // A hidden value is never drawn, old or new: the row says only that it changed.
        if (row.state === "changed" && row.draft !== null) {
          if (hiddenValue) return <Masked />;
          const before = oneLine(row.current ?? "");
          const after = oneLine(row.draft);
          return (
            <span className="inline-flex max-w-full min-w-0 items-center gap-2 align-middle">
              <Mono truncate title={row.current ?? ""} tone="muted" className="max-w-40 line-through">
                {before.text === "" ? t("environment.empty") : before.text}
              </Mono>
              <span aria-hidden="true" className="text-fg-faint">
                →
              </span>
              <span className="sr-only">{t("environment.reviewDialog.becomesSrOnly")}</span>
              <Mono truncate title={row.draft} className="max-w-40">
                {after.text === "" ? t("environment.empty") : after.text}
              </Mono>
            </span>
          );
        }
        const shown = inView(row);
        const value = shown ? valueOf(row) : null;
        const hideable = row.state !== "removed" && row.current !== null && isMasked(row.current) && row.draft === null && expressionOf(row) === null;
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
        const first = oneLine(value);
        return (
          <span className="inline-flex max-w-full min-w-0 items-center gap-1 align-middle">
            <Mono truncate title={value} tone={row.state === "removed" ? "muted" : "default"} className={cx("max-w-72", row.state === "removed" && "line-through")}>
              {first.text}
            </Mono>
            {first.lines > 1 ? <span className="shrink-0 text-12 text-fg-faint">{t("environment.lines", { count: first.lines })}</span> : null}
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
          <MenuItem icon={<Pencil />} onClick={() => edit(row)}>
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
          <MenuItem icon={<OBJECT_ICONS.rollback />} onClick={() => undo(row.name)}>
            {t("environment.tab.undo")}
          </MenuItem>
        )}
        {row.state !== "removed" ? (
          <>
            <MenuSeparator />
            <MenuGroup label={t("variables.audience.groupLabel")}>
              {AUDIENCES.map((option) => {
                const current = option === effectiveAudience(recordedAudience, staged, row.name);
                return (
                  <MenuItem key={option} icon={AUDIENCE_ICON[option]} description={audienceHelp(option, t.locale)} disabled={current} onClick={() => stageAudience(row.name, option)}>
                    {audienceLabel(option, t.locale)}
                    {current ? " " : null}
                    {current ? <span className="sr-only">{t("variables.audience.currentSrOnly")}</span> : null}
                  </MenuItem>
                );
              })}
            </MenuGroup>
          </>
        ) : null}
        {choice !== null ? (
          <>
            <MenuSeparator />
            <MenuGroup label={t("environment.secrecy.groupLabel")}>
              {SECRECY_CHOICES.map((option) => (
                <MenuItem
                  key={option}
                  icon={SECRECY_ICON[option]}
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
      {hasReferences ? (
        <Button icon={<VERB_ICONS.refresh aria-hidden="true" />} loading={resolve.isPending} onClick={() => resolve.mutate()}>
          {t("variables.env.resolveAction")}
        </Button>
      ) : null}
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
            // The row opens its variable: the menu keeps what is not an edit (remove, who gets it, secrecy).
            onRowActivate={(row) => {
              if (row.state !== "removed") edit(row);
            }}
            density="compact"
          />
        )}
      </QueryState>
      {app.data?.app_type === "docker-compose" ? (
        <ComposeVariables
          domain={domain}
          onAdd={(name) => {
            setEditing({ mode: "add", name });
          }}
        />
      ) : null}
      {env.data !== undefined ? <PredefinedVariables domain={domain} predefined={env.data.predefined} /> : null}
      {empty ? null : (
        <>
          <SaveBar
            changes={changeCount}
            onDiscard={() => {
              forget();
              announce(t("environment.tab.discardedAnnounce"));
            }}
            onSave={() => saveAs(true)}
            saving={save.isPending}
            saveLabel={isStatic ? t("environment.tab.save") : t("environment.tab.saveAndRestart")}
            {...(isStatic
              ? {}
              : {
                  saveMenu: (
                    <MenuItem icon={<Save />} onClick={() => saveAs(false)}>
                        {t("environment.tab.saveOnly")}
                      </MenuItem>
                  ),
                })}
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
        onSubmit={(name, value, audience, comment) => {
          const adding = editing?.mode === "add";
          setEditing(null);
          // A hidden value that was kept is not a change of the value: only its audience or note may be.
          if (value !== null) stage([{ kind: "set", name, value }], adding ? t("environment.tab.willBeAdded", { name }) : t("environment.tab.willChange", { name }));
          if (comment !== commentOf(name)) {
            setStagedComments((current) => new Map(current).set(name, comment));
          }
          if (audience !== effectiveAudience(recordedAudience, staged, name)) {
            setStaged((current) => new Map(current).set(name, audience));
          }
        }}
      />
      <PasteDialog
        open={pasting}
        onOpenChange={setPasting}
        current={masked}
        onStage={(ops, summary, comments) => {
          setPasted(true);
          stage(ops, summary, comments);
        }}
      />
      <ReviewDialog
        domain={domain}
        file={file}
        isStatic={isStatic}
        diff={review}
        merge={merge}
        audience={review === null ? [] : audienceList}
        comments={review === null ? new Map() : reviewComments}
        onClose={() => setReview(null)}
        onSaved={forget}
      />
    </div>
  );
}
