import { Ellipsis, Plus, Power, RotateCw } from "lucide-react";

import { ErrorBlock, SaveBar } from "../components/page";
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  Field,
  IconButton,
  Input,
  Mono,
  Notice,
  Skeleton,
  StatusPill,
} from "../components/ui";
import type { Column } from "../components/ui";
import { DoDont, Section, Stage } from "./gallery";

interface Row {
  name: string;
  state: "running" | "failed";
  size: string;
}

const ROWS: Row[] = [
  { name: "shop_production", state: "running", size: "1.2 GB" },
  { name: "blog", state: "failed", size: "84 MB" },
];

const GOOD: Column<Row>[] = [
  { id: "name", header: "Database", cell: (row) => <Mono>{row.name}</Mono> },
  { id: "state", header: "State", cell: (row) => <StatusPill state={row.state} appearance="inline" size="sm" />, width: "w-32" },
  { id: "size", header: "Size", cell: (row) => row.size, align: "end", mono: true },
];

const BAD: Column<Row>[] = [
  { id: "state", header: "State", cell: (row) => <span className={row.state === "running" ? "text-ok" : "text-fail"}>●</span> },
  { id: "size", header: "Size", cell: (row) => row.size },
  { id: "name", header: "Database", cell: (row) => row.name },
];

const TAXONOMY: [string, string, string, string][] = [
  ["Field error", "A control's value is wrong", "Field error", "Name is required"],
  ["Inline notice", "Something persistent about a form, section or dialog", "Notice", "The plan changes two files"],
  ["Banner", "The state of the page or the server", "Notice variant=banner", "shop.example.com is down"],
  ["Error block", "A load or an action failed, with the system's words", "ErrorBlock, QueryState", "Could not load applications"],
  ["Toast", "The outcome of an action that is not visible on screen", "toast.*", "Update of shop.example.com queued"],
  ["Dialog", "A decision that blocks", "Dialog, ConfirmDialog", "Delete application"],
];

const COPY: [string, string, string][] = [
  ["Releases", "Instant rollback", "Name the benefit, not the directory"],
  ["Enable releases", "Turn on instant rollback", "A verb and the thing"],
  ["Health check", "Startup check", "What it does for the operator"],
  ["Payload URL", "Webhook URL", "The forge's jargon stays in the forge"],
  ["Unit / systemd unit", "Service (the unit name in mono)", "The technical name is a value, not a label"],
  ["Run as an argv, without a shell.", "Runs directly, not through a shell: pipes, && and $VARS do not work.", "Say the consequence"],
  ["Oops! Something went wrong", "Could not save the schedule", "What failed, then the fix, then the system's words"],
];

/** The system's patterns, each as a rule, the way to follow it and the mistake it prevents. */
export function Patterns() {
  return (
    <>
      <Section
        id="p-state"
        title="Showing state"
        description="An entity's state is a StatusPill: pill in a header, inline in a table (second column, 128px), glyph and number in a counter. Whether something is on, shown as a label, is a state badge too: the running green for on, the stopped grey for off."
      >
        <DoDont
          rule="Colour, shape and word, always together"
          doThis={
            <div className="flex flex-wrap gap-3">
              <StatusPill state="running" />
              <StatusPill state="queued" />
              <StatusPill state="failed" appearance="inline" />
            </div>
          }
          notThis={
            <div className="flex flex-wrap gap-3">
              <Badge tone="ok">Running</Badge>
              <span className="text-ok">●</span>
            </div>
          }
          why="A badge is an attribute, and a coloured dot alone is invisible to one man in twelve: the four state colours are nearly one colour to a deuteranope."
        />
        <DoDont
          rule="A feature's on/off as a label is a state badge, never bare text"
          doThis={
            <div className="flex flex-wrap gap-3">
              <StatusPill state="running" label="On" size="sm" />
              <StatusPill state="stopped" label="Off" size="sm" />
            </div>
          }
          notThis={
            <div className="flex flex-wrap gap-3">
              <span className="text-13 text-fg">On</span>
              <span className="text-13 text-fg">Off</span>
            </div>
          }
          why="A word alone has to be read to tell on from off (owner item 56). The badge says it in colour, shape and word, and stays a label: what changes it is a switch or the item's own form."
        />
      </Section>

      <Section
        id="p-actions"
        title="Actions and confirmation"
        description="One primary action per view, last among the header's actions; the rest secondary, then More actions. Confirmation friction is proportional to what can be lost: none, simple, or type the name."
      >
        <DoDont
          rule="One primary action per view"
          doThis={
            <div className="flex flex-wrap gap-2">
              <Button icon={<RotateCw />}>Restart</Button>
              <Button variant="primary">Update</Button>
              <IconButton variant="secondary" label="More actions" icon={<Ellipsis />} />
            </div>
          }
          notThis={
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" icon={<RotateCw />}>
                Restart
              </Button>
              <Button variant="primary">Update</Button>
              <Button variant="primary" icon={<Power />}>
                Stop
              </Button>
            </div>
          }
          why="When everything is violet nothing is. The accent marks the one thing this view is for."
        />
        <DoDont
          rule="Type the name only when the loss is for good"
          doThis={<p className="text-13 text-fg-muted">Remove a backup schedule: a simple confirmation, focus on Cancel. Delete an application: type its name.</p>}
          notThis={<p className="text-13 text-fg-muted">Type “nightly” to remove the nightly schedule, which can be created again in ten seconds.</p>}
          why="Friction everywhere trains people to type without reading. Kept for the irreversible, it makes them read."
        />
      </Section>

      <Section
        id="p-errors"
        title="Errors and system output"
        description="What failed (four words), the fix above, and what the system said below, verbatim in mono, never paraphrased, translated or cut. A field's error is under the field; a load's in its place; an action's where it was started."
      >
        <DoDont
          rule="The system's words, verbatim, under the fix"
          doThis={
            <ErrorBlock
              title="Could not reload nginx"
              hint="Fix the directive on line 14 and test again."
              error={{ detail: 'nginx: [emerg] unknown directive "proxy_pas" in /etc/nginx/sites-enabled/shop:14' }}
            />
          }
          notThis={<p className="text-13 text-fail">Oops! Something went wrong with the web server. Please try again later.</p>}
          why="The operator fixes what nginx said, not what the console guessed it meant."
        />
      </Section>

      <Section
        id="p-empty"
        title="Empty, loading and partial"
        description="Every piece of data has four states: a skeleton shaped like the content, the error, the empty state, the content. A refresh that fails keeps the last answer with the error above it. Nothing that arrives in under a second blinks."
      >
        <DoDont
          rule="An empty section is one line"
          doThis={
            <Card title="Destinations">
              <EmptyState variant="inline" title="No destinations: backups stay on this server." action={<Button size="sm">Add destination</Button>} />
            </Card>
          }
          notThis={
            <div className="flex flex-col gap-3">
              <div className="rounded-card border border-dashed border-border px-6 py-12 text-center text-14">No destinations yet</div>
              <div className="rounded-card border border-dashed border-border px-6 py-12 text-center text-14">No schedules yet</div>
            </div>
          }
          why="Three framed empty states stacked make an empty page longer than a full one. First use is one per page."
        />
        <DoDont
          rule="Load with the shape of what is coming"
          doThis={
            <div aria-busy="true" className="flex flex-col gap-2">
              <Skeleton className="h-4 w-2/5" />
              <Skeleton className="h-3 w-3/5" />
            </div>
          }
          notThis={<p className="text-13 text-fg-muted">Loading...</p>}
          why="A skeleton keeps the room the content takes, so nothing moves when it arrives (CLS 0.05 or less)."
        />
      </Section>

      <Section
        id="p-forms"
        title="Forms and the save bar"
        description="Fields stacked, labels of three words or fewer, the field required by default and Optional marked. Validation on submit, then on leaving a field. A settings subsection is one form saved from its bar; a Switch applies at once and never shares a form with Save."
      >
        <DoDont
          rule="Save is never disabled without a reason on screen"
          doThis={
            <div className="flex flex-col gap-4">
              <Field label="Port" error="Use a port between 1024 and 65535.">
                <Input mono defaultValue="80" />
              </Field>
              <SaveBar changes={1} onDiscard={() => undefined} onSave={() => undefined} />
            </div>
          }
          notThis={
            <div className="flex flex-col gap-4">
              <Field label="Port">
                <Input mono defaultValue="80" />
              </Field>
              <div>
                <Button variant="primary" disabled>
                  Save changes
                </Button>
              </div>
            </div>
          }
          why="A greyed-out button says no without saying why. Let it be pressed and say what is wrong where it is wrong."
        />
      </Section>

      <Section
        id="p-tables"
        title="Tables"
        description="Identity first (it names the row and opens it), state second, attributes, time, numbers right-aligned in mono, row actions last in a menu. Compact rows (36px) for histories, comfortable (44px) for inventories; card rows on a phone."
      >
        <DoDont
          rule="Identity first, state second, numbers right in mono"
          doThis={<DataTable caption="Databases" columns={GOOD} rows={ROWS} getRowId={(row) => row.name} />}
          notThis={<DataTable caption="Databases, wrong order" columns={BAD} rows={ROWS} getRowId={(row) => row.name} />}
          why="A screen reader names a row by its first cell: “shop_production, failed” is a sentence, “red dot, 84 MB” is not."
        />
      </Section>

      <Section
        id="p-notify"
        title="Which channel says it"
        description="An event is said in one channel, never two. A toast is only for an outcome that is not visible on screen, and never for an error that has to be read."
      >
        <Stage plain flush>
          <table className="w-full text-left text-13">
            <thead>
              <tr className="border-b border-border bg-bg-sunken text-12 text-fg-muted">
                <th scope="col" className="h-9 px-3 pl-4 font-medium">
                  Channel
                </th>
                <th scope="col" className="h-9 px-3 font-medium">
                  When
                </th>
                <th scope="col" className="h-9 px-3 font-medium">
                  Component
                </th>
                <th scope="col" className="h-9 px-3 font-medium">
                  Example
                </th>
              </tr>
            </thead>
            <tbody>
              {TAXONOMY.map(([channel, when, component, example]) => (
                <tr key={channel} className="border-b border-border last:border-0">
                  <td className="px-3 py-2.5 pl-4 font-medium">{channel}</td>
                  <td className="px-3 py-2.5 text-fg-muted">{when}</td>
                  <td className="mono px-3 py-2.5 text-12">{component}</td>
                  <td className="px-3 py-2.5 text-fg-muted">{example}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Stage>
      </Section>

      <Section
        id="p-fleet"
        title="Which server am I on"
        description="On a fleet, the server is said in words, in mono, in a fixed place: the top bar, above the page title, in every confirmation and sudo dialog. Never with a tint: colour already means state."
      >
        <DoDont
          rule="Name the server where it matters"
          doThis={<Notice title="Delete shop.example.com on web-2?">The application, its files and its site are removed from web-2.</Notice>}
          notThis={
            <div className="rounded-card border-2 border-accent p-4 text-13">
              <p>Delete shop.example.com?</p>
            </div>
          }
          why="A coloured frame per server is a code to learn, and it collides with state. The name cannot be misread."
        />
      </Section>

      <Section
        id="p-content"
        title="Words"
        description="Direct, technical, no adornment; sentence case; English and Spanish from the typed catalogs, whole sentences with placeholders. A term of Noust's own is introduced by its benefit the first time on a view."
      >
        <Stage plain flush>
          <table className="w-full text-left text-13">
            <thead>
              <tr className="border-b border-border bg-bg-sunken text-12 text-fg-muted">
                <th scope="col" className="h-9 px-3 pl-4 font-medium">
                  Instead of
                </th>
                <th scope="col" className="h-9 px-3 font-medium">
                  Write
                </th>
                <th scope="col" className="h-9 px-3 font-medium">
                  Why
                </th>
              </tr>
            </thead>
            <tbody>
              {COPY.map(([before, after, why]) => (
                <tr key={before} className="border-b border-border last:border-0">
                  <td className="px-3 py-2.5 pl-4 text-fg-muted line-through">{before}</td>
                  <td className="px-3 py-2.5">{after}</td>
                  <td className="px-3 py-2.5 text-fg-muted">{why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Stage>
        <div className="flex flex-wrap gap-2">
          <Button icon={<Plus />}>New application</Button>
          <Button>Add domain</Button>
          <Button>Create backup</Button>
          <Button>Remove destination</Button>
          <Button variant="danger">Delete application</Button>
          <Button>Revoke token</Button>
        </div>
      </Section>
    </>
  );
}
