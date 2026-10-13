import { useQuery } from "@tanstack/react-query";

import { backupDestinationsQuery } from "../../../api/queries/backupDestinations";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Checkbox } from "../../../components/ui/Checkbox";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import type { DestinationDraft } from "./limits";

export interface SendToWords {
  legend: string;
  none: string;
  noneHint: string;
  failed: string;
  keepAll: string;
  countSuffix: string;
  daysSuffix: string;
  /** The accessible name of a destination's count and days boxes. */
  countFor: (destination: string) => string;
  daysFor: (destination: string) => string;
  /** The size limit box, for the policies that have one: its accessible name and its unit. */
  gbFor?: (destination: string) => string;
  gbSuffix?: string;
}

export interface SendToFieldsProps {
  words: SendToWords;
  value: readonly DestinationDraft[];
  onChange: (value: DestinationDraft[]) => void;
  error?: string | undefined;
  /** Whether to read the destinations at all: a closed drawer asks for nothing. */
  enabled?: boolean;
}

/**
 * Where else each backup of a policy goes: the destinations that exist, each with its own
 * retention there. The destinations are the ones every policy shares (Backups, Destinations).
 */
export function SendToFields({ words, value, onChange, error, enabled = true }: SendToFieldsProps) {
  const destinations = useQuery({ ...backupDestinationsQuery(), enabled });
  const available = destinations.data?.destinations ?? [];
  const patch = (name: string, next: Partial<DestinationDraft>): void => onChange(value.map((item) => (item.name === name ? { ...item, ...next } : item)));
  return (
    <fieldset className="flex min-w-0 flex-col gap-3">
      <legend className="mb-3 text-13 font-medium text-fg">{words.legend}</legend>
      {destinations.isError ? (
        <ErrorBlock compact error={destinations.error} title={words.failed} onRetry={() => void destinations.refetch()} />
      ) : available.length === 0 && destinations.data !== undefined ? (
        <EmptyState variant="inline" title={words.none} description={words.noneHint} />
      ) : (
        <ul className="flex flex-col gap-3">
          {available.map((destination) => {
            const chosen = value.find((item) => item.name === destination.name);
            return (
              <li key={destination.name} className="flex flex-col gap-2">
                <Checkbox
                  checked={chosen !== undefined}
                  onCheckedChange={(checked) => onChange(checked ? [...value, { name: destination.name, count: "", days: "" }] : value.filter((item) => item.name !== destination.name))}
                  label={<Mono tone="default">{destination.name}</Mono>}
                />
                {chosen !== undefined ? (
                  <div className="flex flex-wrap gap-3 pl-6">
                    <Input
                      size="sm"
                      mono
                      inputMode="numeric"
                      aria-label={words.countFor(destination.name)}
                      placeholder={words.keepAll}
                      value={chosen.count}
                      onValueChange={(count: string) => patch(destination.name, { count })}
                      suffix={words.countSuffix}
                      className="w-40"
                    />
                    <Input
                      size="sm"
                      mono
                      inputMode="numeric"
                      aria-label={words.daysFor(destination.name)}
                      placeholder={words.keepAll}
                      value={chosen.days}
                      onValueChange={(days: string) => patch(destination.name, { days })}
                      suffix={words.daysSuffix}
                      className="w-40"
                    />
                    {words.gbFor !== undefined ? (
                      <Input
                        size="sm"
                        mono
                        inputMode="decimal"
                        aria-label={words.gbFor(destination.name)}
                        placeholder={words.keepAll}
                        value={chosen.gb ?? ""}
                        onValueChange={(gb: string) => patch(destination.name, { gb })}
                        suffix={words.gbSuffix ?? "GB"}
                        className="w-40"
                      />
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {error !== undefined ? <p className="text-12 text-fail">{error}</p> : null}
    </fieldset>
  );
}
