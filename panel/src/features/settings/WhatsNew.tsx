import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { changelogQuery, releaseNotesQuery } from "../../api/queries/system";
import type { ChangelogEntry } from "../../api/queries/system";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Subsection } from "../../components/page/Subsection";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Disclosure } from "../../components/ui/Disclosure";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { ICONS } from "../../components/ui/icons";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { formatDate } from "../../lib/format";
import { EmptyState } from "../../components/ui/EmptyState";

/** Versions shown at first; the rest are one press away. */
const FIRST = 3;
const MORE = 20;

/** A release's lines. One that needs the operator to act says so in words and a shape, never in colour alone. */
function Lines({ entry }: { entry: Pick<ChangelogEntry, "items"> }) {
  const t = useT();
  const Warn = ICONS.warning;
  return (
    <ul className="flex min-w-0 flex-col gap-1.5">
      {entry.items.map((item, index) => (
        <li key={`${String(index)}-${item.text}`} className="flex min-w-0 max-w-measure items-start gap-2 text-13 text-pretty text-fg">
          {item.action_required ? (
            <span className="inline-flex shrink-0 items-center gap-1 font-medium text-warn">
              <Warn aria-hidden="true" className="size-icon-sm" />
              {t("settings.about.whatsNew.actionRequired")}
            </span>
          ) : (
            <span aria-hidden="true" className="mt-2 size-1 shrink-0 rounded-full bg-fg-faint" />
          )}
          <span className="min-w-0">{item.text}</span>
        </li>
      ))}
    </ul>
  );
}

function day(value: string, locale: Parameters<typeof formatDate>[2]): string {
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : formatDate(parsed, {}, locale);
}

/** Settings > About > What's new: the notes of the versions installed here, from the package itself. */
export function WhatsNew() {
  const t = useT();
  const [all, setAll] = useState(false);
  const notes = useQuery(changelogQuery(MORE));
  const entries = notes.data?.entries ?? [];
  const shown = all ? entries : entries.slice(0, FIRST);
  const body = notes.isError ? (
    <ErrorBlock compact error={notes.error} title={t("settings.about.whatsNew.loadFailed")} />
  ) : notes.data === undefined ? (
    <Card>
      <div aria-hidden="true" className="flex flex-col gap-2">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-3 w-3/4" />
        <Skeleton className="h-3 w-2/3" />
      </div>
    </Card>
  ) : entries.length === 0 ? (
    <EmptyState variant="inline" title={t("settings.about.whatsNew.empty")} />
  ) : (
    <div className="flex min-w-0 flex-col gap-3">
      <Card padding="none">
        <ol className="flex min-w-0 flex-col divide-y divide-border">
          {shown.map((entry) => (
            <li key={entry.version} className="min-w-0 px-5 py-4">
              <Subsection
                title={t("settings.about.whatsNew.version", { version: entry.version })}
                description={[
                  entry.version === notes.data.current_version ? t("settings.about.whatsNew.current") : null,
                  entry.date !== "" ? t("settings.about.whatsNew.releasedOn", { date: day(entry.date, t.locale) }) : null,
                ]
                  .filter((part) => part !== null)
                  .join(" - ")}
              >
                <Lines entry={entry} />
              </Subsection>
            </li>
          ))}
        </ol>
      </Card>
      {entries.length > FIRST ? (
        <div>
          <Button size="sm" variant="ghost" onClick={() => setAll((current) => !current)} aria-expanded={all}>
            {all ? t("settings.about.whatsNew.showFewer") : t("settings.about.whatsNew.showEarlier")}
          </Button>
        </div>
      ) : null}
    </div>
  );
  return (
    <Section title={t("settings.about.whatsNew.title")} description={t("settings.about.whatsNew.description")} loading={notes.isPending}>
      {/* Folded: the notes are read while it is closed, so what arrives never moves what the page is showing. */}
      <Disclosure label={t("settings.about.whatsNew.show")}>{body}</Disclosure>
    </Section>
  );
}

/**
 * What updating to a version will change, before anything is pressed: read by the server from that
 * version's GitHub release (this installation does not have its notes yet), once and for an hour.
 * Where the release cannot be read it says so and still leads to it.
 */
export function UpdateNotes({ version }: { version: string }) {
  const t = useT();
  const notes = useQuery(releaseNotesQuery(version));
  const actions = notes.data?.entry?.items.filter((item) => item.action_required).length ?? 0;
  return (
    <Disclosure label={t("settings.about.updateNotes.summary", { version })}>
      <div className="flex min-w-0 flex-col gap-3">
        {notes.isPending ? (
          <p className="text-13 text-fg-muted">{t("settings.about.updateNotes.loading")}</p>
        ) : notes.isError ? (
          <ErrorBlock compact error={notes.error} title={t("settings.about.updateNotes.unavailable")} />
        ) : notes.data.available && notes.data.entry ? (
          <>
            {actions > 0 ? <p className="text-13 font-medium text-warn">{t("settings.about.updateNotes.actionNeeded", { count: actions })}</p> : null}
            <Lines entry={notes.data.entry} />
          </>
        ) : (
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{notes.data.reason !== "" ? notes.data.reason : t("settings.about.updateNotes.unavailable")}</p>
        )}
        {notes.data?.url ? <ExternalLink href={notes.data.url}>{t("settings.about.updateNotes.openRelease")}</ExternalLink> : null}
      </div>
    </Disclosure>
  );
}
