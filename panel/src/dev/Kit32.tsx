import { useState } from "react";

import { Button, Combobox, FeatureState, Field, FlowDiagram, Mono, Notice, Switch } from "../components/ui";
import type { ComboboxItem } from "../components/ui";
import { BothThemes, DoDont, Item, Row, Section, Stage } from "./gallery";
import {
  PROGGEST_EDGES,
  PROGGEST_LAYERS,
  PROGGEST_LAYERS_BACKEND_DOWN,
  PROGGEST_LOGIN_ROUTE,
  SMALL_EDGES,
  SMALL_LAYERS,
} from "./flowSample";

interface Zone extends ComboboxItem {
  city: string;
  region: string;
  abbreviation: string;
  offset: string;
  minutes: number;
}

// The handful of zones a browser without Intl.supportedValuesOf still shows.
const FALLBACK_ZONES = ["Etc/UTC", "Europe/London", "Europe/Madrid", "Europe/Berlin", "America/New_York", "America/Argentina/Buenos_Aires", "Asia/Kolkata", "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland"];

function offsetOf(zone: string, at: Date): { offset: string; abbreviation: string; minutes: number } {
  const part = (style: "longOffset" | "short"): string =>
    new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: style }).formatToParts(at).find((p) => p.type === "timeZoneName")?.value ?? "";
  const long = part("longOffset").replace("GMT", "UTC");
  const offset = long === "UTC" ? "UTC+00:00" : long;
  const match = /([+-])(\d{2}):(\d{2})/.exec(offset);
  const minutes = match ? (match[1] === "-" ? -1 : 1) * (Number(match[2]) * 60 + Number(match[3])) : 0;
  return { offset, abbreviation: part("short"), minutes };
}

/**
 * What `GET /api/server/clock/timezones` will answer, computed in the browser for the gallery:
 * every zone with its offset now, its abbreviation, its region and a readable city, UTC first,
 * then by offset and name.
 */
function sampleZones(): Zone[] {
  const at = new Date();
  const names = typeof Intl.supportedValuesOf === "function" ? ["Etc/UTC", ...Intl.supportedValuesOf("timeZone")] : FALLBACK_ZONES;
  const zones = [...new Set(names)].map((name): Zone => {
    const [region = name, ...rest] = name.split("/");
    return {
      value: name,
      label: name,
      region,
      city: rest.length > 0 ? rest.join(" / ").replaceAll("_", " ") : name,
      ...offsetOf(name, at),
    };
  });
  return zones.sort((a, b) =>
    a.value === "Etc/UTC" ? -1 : b.value === "Etc/UTC" ? 1 : a.minutes - b.minutes || a.value.localeCompare(b.value),
  );
}

const UNITS: ComboboxItem[] = [
  "nginx.service",
  "noust-web.service",
  "noust-monitor.service",
  "postgresql.service",
  "shop-example-com.service",
  "api-example-com.service",
  "redis-server.service",
].map((unit) => ({ value: unit, label: unit }));

function ZoneOption({ zone }: { zone: Zone }) {
  return (
    <span className="flex min-w-0 items-baseline justify-between gap-3">
      <span className="flex min-w-0 flex-col">
        <Mono truncate>{zone.label}</Mono>
        <span className="truncate text-12 text-fg-muted">{zone.city}</span>
      </span>
      <span className="shrink-0 text-12 text-fg-muted tabular-nums">
        {zone.offset} · {zone.abbreviation}
      </span>
    </span>
  );
}

function Comboboxes() {
  const [zones] = useState(sampleZones);
  const [zone, setZone] = useState("Europe/Madrid");
  const [unit, setUnit] = useState<string | null>(null);
  return (
    <Section
      id="combobox"
      title="Combobox"
      description="Choose one value from a long list by typing. The list filters as you type: by name, by any other text the item carries (a city, an abbreviation) and by offset (+2, utc+1). Arrows move, Enter chooses, Escape closes. Groups keep their headings; beyond a hundred items only the rows in view are rendered. For a short, known list, use Select."
    >
      <Stage>
        <Row className="items-start">
          <Item label={`Time zones: ${String(zones.length)} items, grouped by region, virtualized`} className="w-96 max-w-full">
            <Field label="Time zone" description="Try madrid, cest, +2 or utc-3." className="w-full">
              <Combobox<Zone>
                items={zones}
                value={zone}
                onValueChange={setZone}
                groupBy={(item) => item.region}
                renderItem={(item) => <ZoneOption zone={item} />}
                mono
              />
            </Field>
          </Item>
          <Item label="A short list of system values, not grouped" className="w-80 max-w-full">
            <Field label="Service" optional className="w-full">
              <Combobox items={UNITS} value={unit} onValueChange={setUnit} placeholder="Search services" mono />
            </Field>
          </Item>
          <Item label="Disabled" className="w-64 max-w-full">
            <Combobox aria-label="Service (disabled)" items={UNITS} defaultValue="nginx.service" disabled mono />
          </Item>
        </Row>
      </Stage>
    </Section>
  );
}

function FeatureStates() {
  const [on, setOn] = useState(false);
  return (
    <Section
      id="feature-state"
      title="Feature state"
      description="Whether a feature is on, at the top of the place that configures it: a colour, a glyph and a word at once, so it is read without reading. Green with a switch drawn on; grey with a switch drawn off and the way to turn it on beside the title; amber with a warning when it is on but cannot do what it says. It pulses once when it changes, never with reduced motion."
    >
      <BothThemes>
        <div className="flex flex-col gap-3">
          <FeatureState state="on" title="Notifications are on">
            Failed deploys, expiring certificates and down applications go to 2 channels.
          </FeatureState>
          <FeatureState state="off" title="Instant rollback is off" action={<Button size="sm">Turn on</Button>}>
            Each deploy replaces the last one; going back means building the old version again.
          </FeatureState>
          <FeatureState state="problem" title="Notifications are on, but no channel is set up" action={<Button size="sm">Add a channel</Button>}>
            Nothing is sent until a channel exists.
          </FeatureState>
        </div>
      </BothThemes>
      <Stage>
        <div className="flex max-w-measure flex-col gap-4">
          <Switch label="Scheduled backups" checked={on} onCheckedChange={setOn} />
          <FeatureState state={on ? "on" : "off"} {...(on ? {} : { action: <Button size="sm" onClick={() => setOn(true)}>Enable</Button> })}>
            {on ? "A backup runs every night at 03:00 and the last 7 are kept." : "No backup runs on its own; only the ones you start by hand exist."}
          </FeatureState>
        </div>
      </Stage>
      <DoDont
        rule="The state of a feature is a FeatureState, never a neutral Notice"
        doThis={
          <FeatureState state="off" action={<Button size="sm">Enable</Button>}>
            Pushes to the branch do not deploy by themselves.
          </FeatureState>
        }
        notThis={<Notice title="Automatic deploys: off">Pushes to the branch do not deploy by themselves.</Notice>}
        why="A neutral notice changes only a word between on and off: the operator has to read it to know. A FeatureState says it in colour, shape and word."
      />
    </Section>
  );
}

function FlowDiagrams() {
  const [route, setRoute] = useState(false);
  return (
    <Section
      id="flow-diagram"
      title="Flow diagram"
      description="How requests travel through a web server, in fixed columns: ports, names, locations, destinations, backends. Each element has its own outline, icon and text; colour only says whether it responds. The dashes move along each connection (faster over TLS, a double line for WebSocket) and stop with reduced motion. Point at or focus an element to light its whole way; press it to keep it lit. Under it, a written summary and the same connections as a table; on a phone, a list per column."
    >
      <Stage flush plain className="p-4">
        <div className="flex flex-col gap-4">
          <Switch
            label={
              <>
                Show the route of <Mono>/api/v1/auth/login</Mono>
              </>
            }
            checked={route}
            onCheckedChange={setRoute}
          />
          <FlowDiagram
            label="How proggest.es answers"
            layers={PROGGEST_LAYERS}
            edges={PROGGEST_EDGES}
            {...(route ? { highlight: PROGGEST_LOGIN_ROUTE } : {})}
          />
        </div>
      </Stage>
      <Stage flush plain className="p-4">
        <FlowDiagram label="proggest.es with its backend down" layers={PROGGEST_LAYERS_BACKEND_DOWN} edges={PROGGEST_EDGES} table={false} />
      </Stage>
      <BothThemes>
        <FlowDiagram label="How shop.example.com answers" layers={SMALL_LAYERS} edges={SMALL_EDGES} />
      </BothThemes>
    </Section>
  );
}

/** Specimens of the components added in 3.2. */
export function Kit32() {
  return (
    <>
      <Comboboxes />
      <FeatureStates />
      <FlowDiagrams />
    </>
  );
}
