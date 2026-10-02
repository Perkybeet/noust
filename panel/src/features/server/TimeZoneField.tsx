/**
 * Choosing a time zone (owner item 54, spec 3.2 section 8.1): the kit's Combobox over the zones
 * the server itself knows (`GET /api/server/clock/timezones`), each with its offset now and its
 * abbreviation, searched by name, city, abbreviation or offset (`+2`, `madrid`), grouped by
 * region; the zone in use marked; and under it the time the server will have with the one
 * chosen. A server too old to list its zones (a 3.1 node behind a 3.2 central) says so, and
 * the name is typed, as before.
 */

import { useQuery } from "@tanstack/react-query";

import { isApiError } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import { useNow } from "../../components/page/clock";
import { Combobox } from "../../components/ui/Combobox";
import type { ComboboxItem } from "../../components/ui/Combobox";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { ServerErrorBlock } from "./errors";
import { timezonesQuery } from "./queries";

type Zone = ResponseOf<"/api/server/clock/timezones", "get">["timezones"][number];

export interface ZoneItem extends ComboboxItem {
  city: string;
  region: string;
  abbreviation: string;
  offset: string;
  minutes: number;
}

export function zoneItems(zones: readonly Zone[]): ZoneItem[] {
  return zones.map((zone) => ({
    value: zone.name,
    label: zone.name,
    city: zone.city,
    region: zone.region,
    abbreviation: zone.abbreviation,
    offset: zone.offset,
    minutes: zone.offset_minutes,
  }));
}

/**
 * The time a zone has at `now`, from its offset as the server computed it: the browser's own
 * zone database is not asked, so the answer is the server's.
 */
export function timeInZone(now: number, offsetMinutes: number, locale: string): string {
  const shifted = new Date(now + offsetMinutes * 60_000);
  return new Intl.DateTimeFormat(locale, { timeZone: "UTC", weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(shifted);
}

/** A server that does not have the endpoint yet answers 404 (or 405 through an older proxy). */
function olderServer(error: unknown): boolean {
  return isApiError(error) && (error.status === 404 || error.status === 405);
}

function ZoneOption({ zone, current, t }: { zone: ZoneItem; current: boolean; t: T }) {
  return (
    <span className="flex min-w-0 flex-1 items-baseline justify-between gap-3">
      <span className="flex min-w-0 flex-col">
        <span className="flex min-w-0 items-baseline gap-2">
          <Mono truncate>{zone.label}</Mono>
          {current ? <span className="shrink-0 text-12 text-fg-muted">{t("server.clock.zoneCurrent")}</span> : null}
        </span>
        <span className="truncate text-12 text-fg-muted">{zone.city}</span>
      </span>
      <span className="shrink-0 text-12 text-fg-muted tabular-nums">{`${zone.offset} · ${zone.abbreviation}`}</span>
    </span>
  );
}

/** The time the chosen zone gives, ticking: outside the live region, so it is not re-announced. */
function ClockReading({ zone, t }: { zone: ZoneItem; t: T }) {
  const now = useNow(() => 30_000);
  return <p className="text-13 text-pretty text-fg">{t.rich("server.clock.zoneTime", { time: <Mono>{timeInZone(now, zone.minutes, t.locale)}</Mono> })}</p>;
}

/**
 * What the chosen zone means. Only the offset is live, announced when the choice changes; the
 * region stays mounted while nothing is chosen so the first choice is announced too.
 */
function ResultingTime({ zone, t }: { zone: ZoneItem | undefined; t: T }) {
  return (
    <div className="flex flex-col gap-1">
      <p role="status" className="text-13 text-pretty text-fg empty:hidden">
        {zone !== undefined ? t.rich("server.clock.zoneOffset", { offset: <Mono>{`${zone.offset} · ${zone.abbreviation}`}</Mono> }) : null}
      </p>
      {zone !== undefined ? <ClockReading zone={zone} t={t} /> : null}
    </div>
  );
}

export interface TimeZoneFieldProps {
  value: string;
  onValueChange: (value: string) => void;
  /** The zone the server has now, marked in the list. */
  current: string | null;
  label: string;
  description?: string;
}

/** A time zone field: the combobox over the server's zones, or a typed name on an older server. */
export function TimeZoneField({ value, onValueChange, current, label, description }: TimeZoneFieldProps) {
  const t = useT();
  const zones = useQuery(timezonesQuery());

  if (zones.isError && olderServer(zones.error)) {
    return (
      <div className="flex flex-col gap-2">
        <Field label={label} description={t("server.clock.zoneOlderServer")}>
          <Input mono value={value} onValueChange={onValueChange} autoComplete="off" spellCheck={false} />
        </Field>
      </div>
    );
  }
  if (zones.isError) {
    return <ServerErrorBlock compact error={zones.error} title={t("server.clock.zonesFailed")} onRetry={() => void zones.refetch()} />;
  }
  if (zones.data === undefined) {
    return (
      <div aria-busy="true" className="flex flex-col gap-2">
        <span className="sr-only">{t("server.clock.zonesLoading")}</span>
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-control-md w-full rounded-control" />
      </div>
    );
  }
  const items = zoneItems(zones.data.timezones);
  const chosen = items.find((item) => item.value === value);
  return (
    <div className="flex flex-col gap-3">
      <Field label={label} {...(description !== undefined ? { description } : {})}>
        <Combobox
          items={items}
          value={value}
          onValueChange={(next) => onValueChange(next)}
          groupBy={(item) => item.region}
          renderItem={(item) => <ZoneOption zone={item} current={item.value === current} t={t} />}
          mono
          placeholder={t("server.clock.zoneSearch")}
        />
      </Field>
      <ResultingTime zone={chosen} t={t} />
    </div>
  );
}
