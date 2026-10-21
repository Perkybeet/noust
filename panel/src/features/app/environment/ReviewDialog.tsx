import { Minus, Pencil, Plus } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Mono } from "../../../components/ui/Mono";
import { RowList } from "../../../components/ui/RowList";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { audienceBody, audienceLabel } from "./audience";
import type { AudienceChange } from "./audience";
import type { EnvChange, EnvDiff, EnvMerge } from "./draft";
import { describeCounts } from "./draft";
import { nameProblem, oneLine } from "./dotenv";
import { useSaveEnvironment } from "./useSaveEnvironment";

function kindOf(t: T, kind: EnvChange["kind"]): { label: string; icon: ReactNode } {
  if (kind === "added") return { label: t("environment.added"), icon: <Plus aria-hidden="true" className="size-icon-sm" /> };
  if (kind === "changed") return { label: t("environment.changed"), icon: <Pencil aria-hidden="true" className="size-icon-sm" /> };
  return { label: t("environment.removed"), icon: <Minus aria-hidden="true" className="size-icon-sm" /> };
}

function Value({ value, shown, struck = false }: { value: string | null; shown: boolean; struck?: boolean }) {
  const t = useT();
  if (value === null) return null;
  if (!shown) {
    return (
      <span className="text-fg-faint">
        <span aria-hidden="true" className="text-13 leading-none tracking-widest">
          ••••••••
        </span>
        <span className="sr-only">{t("environment.reviewDialog.hiddenSrOnly")}</span>
      </span>
    );
  }
  if (value === "") return <span className="text-fg-muted">{t("environment.empty")}</span>;
  const { text, lines } = oneLine(value);
  return (
    <span className="flex min-w-0 items-baseline gap-2">
      <Mono truncate title={value} tone={struck ? "muted" : "default"} className={cx("min-w-0", struck && "line-through")}>
        {text}
      </Mono>
      {lines > 1 ? <span className="shrink-0 text-fg-faint">{t("environment.lines", { count: lines })}</span> : null}
    </span>
  );
}

function ChangeRow({ change, shown }: { change: EnvChange; shown: boolean }) {
  const t = useT();
  const kind = kindOf(t, change.kind);
  return (
    <li className="flex flex-col gap-1 px-3 py-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <span className="flex w-24 shrink-0 items-center gap-1.5 text-12 text-fg-muted">
          {kind.icon}
          {kind.label}
        </span>
        <span className="w-56 max-w-full min-w-0 shrink-0 font-medium">
          <Mono truncate title={change.name}>
            {change.name}
          </Mono>
        </span>
        <span className="flex min-w-0 flex-1 basis-48 items-center gap-2 text-12">
          {change.kind === "changed" ? (
            <>
              <Value value={change.before} shown={shown} struck />
              <span aria-hidden="true" className="text-fg-faint">
                →
              </span>
              <span className="sr-only">{t("environment.reviewDialog.becomesSrOnly")}</span>
              <Value value={change.after} shown={shown} />
            </>
          ) : change.kind === "removed" ? (
            <Value value={change.before} shown={shown} struck />
          ) : (
            <Value value={change.after} shown={shown} />
          )}
        </span>
      </div>
    </li>
  );
}

export interface ReviewDialogProps {
  domain: string;
  /** Where the file is, for the description. */
  file: string;
  /** A static site has no process to restart. */
  isStatic: boolean;
  /** What saving changes, or null when the dialog is closed. */
  diff: EnvDiff | null;
  /** What saving sends: the variables it sets and the ones it removes. */
  merge: EnvMerge;
  /** Who gets which variable, where that changes: saved with the values. */
  audience?: readonly AudienceChange[];
  /** The notes above variables that change (name to text, empty to take one away): saved with the values. */
  comments?: ReadonlyMap<string, string>;
  onClose: () => void;
  /** The change was written. */
  onSaved: () => void;
}

/**
 * The last look before a large or pasted change is written: what is added, changed and removed,
 * over the rows the table drew, with no value read in clear. A small change does not come here:
 * it is saved from the bar, and its diff is in the table. Saving writes only the variables that
 * change; "Save and restart" also restarts the app so it runs with them.
 */
export function ReviewDialog({ domain, file, isStatic, diff, merge, audience = [], comments, onClose, onSaved }: ReviewDialogProps) {
  const t = useT();
  const [shown, setShown] = useState(false);
  const commentCount = comments?.size ?? 0;
  const save = useSaveEnvironment(domain);

  const close = (): void => {
    if (save.isPending) return;
    onClose();
    setShown(false);
    save.reset();
  };

  const write = (restart: boolean): void => {
    save.mutate(
      {
        merge,
        ...(audience.length > 0 ? { audience: audienceBody(audience) } : {}),
        ...(commentCount > 0 ? { comments: Object.fromEntries(comments ?? []) } : {}),
        restart: restart && !isStatic,
      },
      {
        onSuccess: () => {
          onSaved();
          onClose();
          setShown(false);
        },
      },
    );
  };

  const changes = diff?.changes ?? [];
  const nothing = changes.length === 0 && audience.length === 0 && commentCount === 0;
  const counts = {
    added: changes.filter((c) => c.kind === "added").length,
    changed: changes.filter((c) => c.kind === "changed").length,
    removed: changes.filter((c) => c.kind === "removed").length,
  };
  // A name the API refuses blocks the whole save, so it is said before the button is pressed.
  const invalid = Object.keys(merge.set).filter((name) => nameProblem(name) !== null);
  const blocked = nothing || invalid.length > 0;

  return (
    <Dialog
      open={diff !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
      size="lg"
      title={t("environment.reviewDialog.title")}
      description={t.rich("environment.reviewDialog.description", {
        file: <Mono className="break-all">{file}</Mono>,
      })}
      footer={
        <>
          <Button disabled={save.isPending} onClick={close}>
            {t("environment.cancel")}
          </Button>
          {isStatic ? (
            <Button variant="primary" loading={save.isPending} disabled={blocked} onClick={() => write(false)}>
              {t("environment.reviewDialog.saveChanges")}
            </Button>
          ) : (
            <>
              <Button loading={save.isPending && !save.variables.restart} disabled={blocked || save.isPending} onClick={() => write(false)}>
                {t("environment.tab.saveOnly")}
              </Button>
              <Button variant="primary" loading={save.isPending && save.variables.restart} disabled={blocked || save.isPending} onClick={() => write(true)}>
                {t("environment.tab.saveAndRestart")}
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-13 text-fg">
            {nothing
              ? t("environment.reviewDialog.nothingChanges")
              : changes.length === 0 && commentCount > 0 && audience.length === 0
                ? t("environment.reviewDialog.onlyComments", { count: commentCount })
                : changes.length === 0
                  ? t("variables.review.onlyAudience")
                  : `${describeCounts(counts, t.locale)}. ${t("environment.reviewDialog.staySummary", { count: diff?.unchanged ?? 0 })}`}
          </p>
          {changes.length > 0 ? <Switch label={t("environment.reviewDialog.showValues")} checked={shown} onCheckedChange={setShown} /> : null}
        </div>

        {invalid.length > 0 ? (
          <ErrorBlock
            compact
            title={t("environment.reviewDialog.invalidNamesTitle")}
            hint={t("environment.reviewDialog.invalidNamesHint")}
            error={{ detail: invalid.map((name) => nameProblem(name, t.locale)).join("\n") }}
          />
        ) : null}

        {changes.length > 0 ? (
          // Scrolls on its own so a long review never pushes Save below the window.
          <div
            role="region"
            aria-label={t("environment.reviewDialog.changesRegionAria")}
            tabIndex={0}
            className="max-h-80 overflow-y-auto rounded-control border border-border bg-bg-sunken scroll-thin -outline-offset-2"
          >
            <ul className="divide-y divide-border">
              {changes.map((change) => (
                <ChangeRow key={change.name} change={change} shown={shown} />
              ))}
            </ul>
          </div>
        ) : null}

        {commentCount > 0 ? (
          <div className="flex flex-col gap-2">
            <p className="text-13 font-medium text-fg">{t("environment.reviewDialog.commentsTitle")}</p>
            <RowList aria-label={t("environment.reviewDialog.commentsTitle")}>
              {[...(comments ?? [])].map(([name, text]) => (
                <RowList.Item key={name}>
                  <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1 text-12">
                    <Mono truncate title={name}>
                      {name}
                    </Mono>
                    <span className="min-w-0 text-fg-muted">{text === "" ? t("environment.reviewDialog.commentRemoved") : text}</span>
                  </div>
                </RowList.Item>
              ))}
            </RowList>
          </div>
        ) : null}

        {audience.length > 0 ? (
          <div className="flex flex-col gap-2">
            <p className="text-13 font-medium text-fg">{t("variables.review.audienceTitle")}</p>
            <RowList aria-label={t("variables.review.audienceTitle")}>
              {audience.map((change) => (
                <RowList.Item key={change.name}>
                  <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-12">
                    <span className="w-56 max-w-full min-w-0 shrink-0 font-medium">
                      <Mono truncate title={change.name}>
                        {change.name}
                      </Mono>
                    </span>
                    <span className="text-fg-muted line-through">{audienceLabel(change.before, t.locale)}</span>
                    <span aria-hidden="true" className="text-fg-faint">
                      →
                    </span>
                    <span className="sr-only">{t("environment.reviewDialog.becomesSrOnly")}</span>
                    <span className="text-fg">{audienceLabel(change.after, t.locale)}</span>
                  </div>
                </RowList.Item>
              ))}
            </RowList>
          </div>
        ) : null}

        {save.isError ? <ErrorBlock live compact error={save.error} title={t("environment.reviewDialog.saveFailed")} /> : null}
      </div>
    </Dialog>
  );
}
