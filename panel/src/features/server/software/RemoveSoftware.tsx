/**
 * Removing a piece of the server's software (item 118): first what it would do, asked of the server, and then one
 * of three answers. The server may refuse, because something still uses the piece, and it says why and what; the
 * refusal is the manager's, never this screen's. It may say the piece is not Noust's, which asks for more than a
 * click. Or it is one question. Nothing is removed until the person says so, and then it is a task.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { Badge } from "../../../components/ui/Badge";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Dialog } from "../../../components/ui/Dialog";
import { ICONS } from "../../../components/ui/icons";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Mono } from "../../../components/ui/Mono";
import { ServerText } from "../../../components/ui/ServerText";
import { Skeleton, SkeletonText } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { asCoded } from "../../../lib/serverSentence";
import { ServerErrorBlock, useServerConfirm } from "../errors";
import { useServerJob } from "../serverJob";
import { DependentList } from "./Dependents";
import { dependentPage, isDependentKind } from "./registry";
import { impactQuery, removeSoftware } from "./queries";
import type { Blocker, Dependent, Impact, SoftwareItem } from "./queries";
import { componentName, dependentsSentence, purposeOf } from "./words";

function Packages({ names }: { names: readonly string[] }) {
  return (
    <ul className="mt-1 flex flex-wrap gap-1">
      {names.map((name) => (
        <li key={name}>
          <Badge mono>{name}</Badge>
        </li>
      ))}
    </ul>
  );
}

/** What removing would do, laid out before anything is touched: the packages, what goes with them, what depends. */
function ImpactSummary({ impact }: { impact: Impact }) {
  const t = useT();
  const removes = impact.removes ?? [];
  const also = impact.also_removes ?? [];
  const files = impact.repository_files ?? [];
  const dependents = impact.dependents ?? [];
  return (
    <div className="flex flex-col gap-3 text-13">
      <dl className="flex flex-col gap-3">
        <div>
          <dt className="font-medium text-fg">{t("software.remove.packages")}</dt>
          <dd>{removes.length > 0 ? <Packages names={removes} /> : null}</dd>
        </div>
        <div>
          <dt className="font-medium text-fg">{t("software.remove.alsoRemoves")}</dt>
          <dd>{also.length > 0 ? <Packages names={also} /> : <p className="mt-1 text-fg-muted">{t("software.remove.nothingElse")}</p>}</dd>
        </div>
        <div>
          <dt className="font-medium text-fg">{t("software.remove.dependents")}</dt>
          <dd className="mt-1">{dependents.length > 0 ? <DependentList dependents={dependents} /> : <p className="text-fg-muted">{t("software.remove.nothingDepends")}</p>}</dd>
        </div>
        {files.length > 0 ? (
          <div>
            <dt className="font-medium text-fg">{t("software.remove.repository")}</dt>
            <dd>
              <ul className="mt-1 flex flex-col gap-0.5">
                {files.map((file) => (
                  <li key={file}>
                    <Mono tone="muted" className="text-12">
                      {file}
                    </Mono>
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        ) : null}
      </dl>
      <p className="text-12 text-fg-muted">{t("software.remove.notPurged")}</p>
    </div>
  );
}

/** The server's refusal, in its words, with what stands in the way and the way to it. */
function Refused({ item, blocker, onClose }: { item: SoftwareItem; blocker: Blocker; onClose: () => void }) {
  const t = useT();
  const name = componentName(t, item.id);
  const dependents: readonly Dependent[] = blocker.dependents ?? [];
  // The sentence is the server's, by its code; the list of what depends is said by the console in its own language, because
  // the one the server put in the sentence is English ("4 sites") and a Spanish sentence would carry it.
  const params = { ...(blocker.params ?? {}), ...(dependents.length > 0 && blocker.params?.["dependents"] !== undefined ? { dependents: dependentsSentence(t, dependents) } : {}) };
  const coded = asCoded({ code: blocker.code, params });
  const first = dependents.find((dependent) => dependentPage(dependent.kind) !== null);
  const page = first === undefined ? null : dependentPage(first.kind);
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="md"
      title={t("software.refused.title", { name })}
      footer={
        <>
          {page !== null && first !== undefined && isDependentKind(first.kind) && first.kind !== "console" && first.kind !== "unreadable" ? (
            <Link to={page} className={buttonClassName("secondary")} onClick={onClose}>
              {t(`software.refused.see.${first.kind}`)}
            </Link>
          ) : null}
          <Button variant="primary" onClick={onClose}>
            {t("software.refused.understood")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="flex items-start gap-2 text-14 text-fg">
          <ICONS.warning aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-warn" />
          <span>
            <ServerText coded={coded} text={blocker.text} />
          </span>
        </p>
        {dependents.length > 0 ? <DependentList dependents={dependents} /> : null}
        <p className="text-13 text-pretty text-fg-muted">
          <ServerText coded={coded} text={blocker.hint} part="hint" />
        </p>
      </div>
    </Dialog>
  );
}

/** The first look, while the server asks the package manager what removing would do: a second, about. */
function Looking({ item, onClose }: { item: SoftwareItem; onClose: () => void }) {
  const t = useT();
  const name = componentName(t, item.id);
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="md"
      title={t("common.words.removeNamed", { name })}
      footer={<Button onClick={onClose}>{t("common.confirmDialog.cancel")}</Button>}
    >
      <LoadingRegion label={t("software.remove.looking", { name })}>
        <div className="flex flex-col gap-3">
          <Skeleton className="h-4 w-1/3" />
          <SkeletonText lines={2} />
          <Skeleton className="h-4 w-1/2" />
        </div>
      </LoadingRegion>
    </Dialog>
  );
}

/** The question itself: one click for what Noust put there, a name and a yes for what it did not. */
function Question({ item, impact, onClose }: { item: SoftwareItem; impact: Impact; onClose: () => void }) {
  const t = useT();
  const confirmation = useServerConfirm();
  const jobs = useServerJob();
  const name = componentName(t, item.id);
  const purpose = purposeOf(item);
  const unknown = impact.needs_confirmation;
  const [sure, setSure] = useState(false);
  const common = {
    open: true,
    onOpenChange: (open: boolean) => {
      if (!open) onClose();
    },
    size: "md" as const,
    title: t("common.words.removeNamed", { name }),
    // What it is for, so the question says what stops working; for what Noust did not put there, the stronger warning.
    description: unknown ? t("software.remove.unknownOrigin") : <ServerText coded={purpose.coded} text={purpose.text} />,
    actionLabel: t("common.words.removeNamed", { name }),
    destructive: true,
    ready: !unknown || sure,
    onConfirm: async () => {
      const accepted = await removeSoftware(item.id, unknown);
      jobs.track(accepted.job_id, "software");
    },
    ...confirmation,
  };
  const body = (
    <div className="flex flex-col gap-4">
      <ImpactSummary impact={impact} />
      {unknown ? <Checkbox checked={sure} onCheckedChange={setSure} label={t("software.remove.sure")} /> : null}
    </div>
  );
  return unknown ? (
    <ConfirmDialog {...common} friction="type" confirmText={item.id}>
      {body}
    </ConfirmDialog>
  ) : (
    <ConfirmDialog {...common} friction="simple">
      {body}
    </ConfirmDialog>
  );
}

/** Removing a piece: looks at what it would do, then asks, refuses or says what the server could not tell. */
export function RemoveSoftware({ item, onClose }: { item: SoftwareItem; onClose: () => void }) {
  const t = useT();
  const impact = useQuery(impactQuery(item.id));
  if (impact.isPending) return <Looking item={item} onClose={onClose} />;
  if (impact.isError) {
    return (
      <Dialog
        open
        onOpenChange={(open) => {
          if (!open) onClose();
        }}
        size="md"
        title={t("common.words.removeNamed", { name: componentName(t, item.id) })}
        footer={<Button onClick={onClose}>{t("common.words.close")}</Button>}
      >
        <ServerErrorBlock error={impact.error} title={t("software.remove.loadFailed", { name: componentName(t, item.id) })} onRetry={() => void impact.refetch()} retrying={impact.isRefetching} />
      </Dialog>
    );
  }
  const first = impact.data.blockers?.[0];
  if (first !== undefined) return <Refused item={item} blocker={first} onClose={onClose} />;
  return <Question item={item} impact={impact.data} onClose={onClose} />;
}
