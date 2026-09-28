import { useMutation } from "@tanstack/react-query";
import { CircleCheck, Minus, Pencil, Plus } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../../api/client";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { useAppActions } from "../../apps/useAppActions";
import type { EnvChange, EnvDiff } from "./draft";
import { describeCounts } from "./draft";
import { nameProblem } from "./dotenv";

function kindOf(t: T, kind: EnvChange["kind"]): { label: string; icon: ReactNode } {
  if (kind === "added") return { label: t("environment.added"), icon: <Plus aria-hidden="true" className="size-3.5" /> };
  if (kind === "changed") return { label: t("environment.changed"), icon: <Pencil aria-hidden="true" className="size-3.5" /> };
  return { label: t("environment.removed"), icon: <Minus aria-hidden="true" className="size-3.5" /> };
}

function Value({ value, shown, struck = false }: { value: string | null; shown: boolean; struck?: boolean }) {
  const t = useT();
  if (value === null) return null;
  if (!shown) {
    return (
      <span className="text-fg-faint">
        <span aria-hidden="true" className="text-13 leading-none tracking-[0.08em]">
          ••••••••
        </span>
        <span className="sr-only">{t("environment.reviewDialog.hiddenSrOnly")}</span>
      </span>
    );
  }
  if (value === "") return <span className="text-fg-faint">{t("environment.empty")}</span>;
  return (
    <span translate="no" title={value} className={cx("mono min-w-0 truncate", struck ? "text-fg-muted line-through" : "text-fg")}>
      {value}
    </span>
  );
}

function ChangeRow({ change, shown }: { change: EnvChange; shown: boolean }) {
  const t = useT();
  const kind = kindOf(t, change.kind);
  return (
    <li className="flex flex-col gap-1 px-3 py-2">
      <div className="grid grid-cols-[6.5rem_minmax(0,1fr)] items-center gap-x-3 gap-y-1 sm:grid-cols-[6.5rem_minmax(0,14rem)_minmax(0,1fr)]">
        <span className="flex items-center gap-1.5 text-12 text-fg-muted">
          {kind.icon}
          {kind.label}
        </span>
        <span translate="no" title={change.name} className="mono truncate text-12 font-medium text-fg">
          {change.name}
        </span>
        <span className="col-span-2 flex min-w-0 items-center gap-2 text-12 sm:col-span-1">
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
  onClose: () => void;
  /** The file now holds `next`. */
  onSaved: (next: Map<string, string>) => void;
}

/**
 * The last look before the file is rewritten: what is added, changed and removed, over the
 * values the file really holds. Saving sends the complete map; afterwards the app is offered
 * a restart, since its process keeps the environment it started with.
 */
export function ReviewDialog({ domain, file, isStatic, diff, onClose, onSaved }: ReviewDialogProps) {
  const t = useT();
  const [shown, setShown] = useState(false);
  const [saved, setSaved] = useState(false);
  const { restart } = useAppActions(domain);

  const save = useMutation({
    mutationFn: (next: Map<string, string>) =>
      request("put", "/api/apps/{domain}/env", { params: { domain }, body: { variables: Object.fromEntries(next) } }),
    onSuccess: (_result, next) => {
      setSaved(true);
      onSaved(next);
    },
  });

  const close = (): void => {
    if (save.isPending) return;
    onClose();
    setSaved(false);
    setShown(false);
    save.reset();
  };

  const changes = diff?.changes ?? [];
  const counts = {
    added: changes.filter((c) => c.kind === "added").length,
    changed: changes.filter((c) => c.kind === "changed").length,
    removed: changes.filter((c) => c.kind === "removed").length,
  };
  // A name the file already had that the API refuses blocks the whole save: it rewrites the file.
  const invalid = diff === null ? [] : [...diff.next.keys()].filter((name) => nameProblem(name) !== null);

  if (saved) {
    return (
      <Dialog
        open={diff !== null}
        onOpenChange={(open) => {
          if (!open) close();
        }}
        size="sm"
        title={t("environment.reviewDialog.savedTitle")}
        description={
          isStatic ? t("environment.reviewDialog.savedStatic", { file }) : t("environment.reviewDialog.savedDynamic", { file, domain })
        }
        footer={
          isStatic ? (
            <Button variant="primary" onClick={close}>
              {t("environment.reviewDialog.done")}
            </Button>
          ) : (
            <>
              <Button disabled={restart.isPending} onClick={close}>
                {t("environment.reviewDialog.restartLater")}
              </Button>
              <Button
                variant="primary"
                loading={restart.isPending}
                onClick={() => {
                  restart.mutate(undefined, { onSuccess: close });
                }}
              >
                {t("environment.reviewDialog.restartNow")}
              </Button>
            </>
          )
        }
      >
        <p className="flex items-center gap-2 text-13 text-fg">
          <CircleCheck aria-hidden="true" className="size-4 shrink-0 text-ok" />
          {`${describeCounts(counts, t.locale)}.`}
        </p>
      </Dialog>
    );
  }

  return (
    <Dialog
      open={diff !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
      size="lg"
      title={t("environment.reviewDialog.title")}
      description={t.rich("environment.reviewDialog.description", {
        file: (
          <code translate="no" className="text-13 break-all">
            {file}
          </code>
        ),
      })}
      footer={
        <>
          <Button disabled={save.isPending} onClick={close}>
            {t("environment.cancel")}
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={changes.length === 0 || invalid.length > 0}
            onClick={() => {
              if (diff !== null) save.mutate(diff.next);
            }}
          >
            {t("environment.reviewDialog.saveChanges")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-13 text-fg">
            {changes.length === 0
              ? t("environment.reviewDialog.nothingChanges")
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
            className="max-h-[min(22rem,38vh)] overflow-y-auto rounded-control border border-border bg-bg-sunken scroll-thin focus-visible:outline-2 focus-visible:outline-focus"
          >
            <ul className="divide-y divide-border">
              {changes.map((change) => (
                <ChangeRow key={change.name} change={change} shown={shown} />
              ))}
            </ul>
          </div>
        ) : null}

        {save.isError ? <ErrorBlock live compact error={save.error} title={t("environment.reviewDialog.saveFailed")} /> : null}
      </div>
    </Dialog>
  );
}
