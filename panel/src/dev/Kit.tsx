import { Boxes, DatabaseBackup, GitBranch, RotateCw } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "../app/PageHeader";
import { FilterBar, JobProgress, Subsection } from "../components/page";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyCell,
  EmptyState,
  Field,
  Input,
  MenuItem,
  MenuSeparator,
  Meter,
  Notice,
  Select,
  StatusPill,
} from "../components/ui";
import type { Column } from "../components/ui";
import { BothThemes, Item, Row, Section, Stage } from "./gallery";
import type { SampleApp } from "./sample";
import { SAMPLE_APPS } from "./sample";

function Notices() {
  return (
    <Section
      id="notice"
      title="Notice"
      description="A persistent message beside what it concerns. Four severities, each a colour, an icon and a word (read by screen readers); information is achromatic. Inline next to a form, section or dialog; banner for the state of a page or a server, between the header and the content. Never tinted by hand."
    >
      <BothThemes>
        <div className="flex flex-col gap-3">
          <Notice title="Noust was called WASM until 3.0">Old commands keep working through the wasm alias.</Notice>
          <Notice tone="success" title="Saved the startup check" onDismiss={() => undefined} />
          <Notice tone="warning" title="The certificate expires in 5 days" action={<Button size="sm">Renew</Button>}>
            Renewal runs by itself 30 days before; this one failed twice.
          </Notice>
          <Notice tone="error" variant="banner" title="shop.example.com is down" action={<Button size="sm">Diagnose</Button>}>
            The service stopped 4 minutes ago and has not come back.
          </Notice>
        </div>
      </BothThemes>
    </Section>
  );
}

function Jobs() {
  return (
    <Section
      id="job-progress"
      title="Job progress"
      description="The one job in hand, shown where the operator started it: waiting (a still ring), running (amber spinner, the verb, the current step verbatim), done (quiet, dismissible) or failed (the fix above, the system's words below). Its history belongs to Activity."
    >
      <BothThemes>
        <div className="flex flex-col gap-3">
          <JobProgress state="queued" title="Backup of shop.example.com" announce={false} />
          <JobProgress state="running" title="Deploying shop.example.com" step="npm run build" announce={false} />
          <JobProgress state="succeeded" title="The certificate covers every domain" onDismiss={() => undefined} announce={false} />
          <JobProgress
            state="failed"
            title="The deploy failed"
            hint="Fix the build script and deploy again; the previous version is still serving."
            error={{ detail: "npm ERR! code ELIFECYCLE\nnpm ERR! errno 1" }}
            onDismiss={() => undefined}
            announce={false}
          />
        </div>
      </BothThemes>
    </Section>
  );
}

function Filters() {
  const [q, setQ] = useState("");
  return (
    <Section
      id="filter-bar"
      title="Filter bar"
      description="Search and filters above every list, the same everywhere: a 288px search box that / focuses, the filters after it, the count and a secondary action at the right. Filters live in the URL."
    >
      <Stage>
        <FilterBar
          label="Filter applications"
          search={{ value: q, onChange: setQ, label: "Search applications", placeholder: "Name or domain" }}
          filters={
            <>
              <Select aria-label="State" value="all" onValueChange={() => undefined} options={[{ value: "all", label: "Every state" }]} className="min-w-36" />
              <Select aria-label="Type" value="all" onValueChange={() => undefined} options={[{ value: "all", label: "Every type" }]} className="min-w-36" />
            </>
          }
          count="3 of 17 applications"
          actions={<Button icon={<RotateCw />}>Refresh</Button>}
        />
      </Stage>
    </Section>
  );
}

const COLUMNS: Column<SampleApp>[] = [
  { id: "domain", header: "Application", cell: (app) => app.domain },
  { id: "state", header: "State", cell: (app) => <StatusPill state={app.status} appearance="inline" size="sm" />, card: "status", width: "w-32" },
  { id: "type", header: "Type", cell: (app) => app.type },
  {
    id: "branch",
    header: "Branch",
    cell: (app) => (app.status === "static" ? <EmptyCell reason="No branch: uploaded files" /> : <span className="mono">main</span>),
  },
  { id: "port", header: "Port", cell: (app) => (app.port === null ? <EmptyCell reason="No port: served as files" /> : `:${String(app.port)}`), align: "end", mono: true },
];

function Empties() {
  return (
    <Section
      id="empty"
      title="Empty states and empty cells"
      description="First use takes the content's place, once per page: what this is, one sentence, the one action possible now and its command. Inline is a line inside a section or a table filtered to nothing, 56px at most. An empty cell is an en dash with its reason for screen readers."
    >
      <div className="grid gap-6 lg:grid-cols-2">
        <Item label="variant=&quot;firstUse&quot;">
          <Stage plain className="w-full">
            <EmptyState
              variant="firstUse"
              icon={<Boxes />}
              title="No applications yet"
              description="Deploy a repository and Noust builds it, runs it as a service and serves it over HTTPS."
              action={<Button variant="primary">New application</Button>}
              command="noust create -d example.com -s https://github.com/you/app"
            />
          </Stage>
        </Item>
        <div className="flex flex-col gap-6">
          <Item label="variant=&quot;inline&quot;" className="w-full">
            <Card title="Schedules" className="w-full">
              <EmptyState variant="inline" title="No schedules: backups run only when you start one." action={<Button size="sm">Add schedule</Button>} />
            </Card>
          </Item>
          <Item label="A filter that matched nothing" className="w-full">
            <Stage plain className="w-full">
              <EmptyState variant="inline" title="No applications match “billing”." action={<Button size="sm" variant="ghost">Clear filters</Button>} />
            </Stage>
          </Item>
        </div>
      </div>
      <Item label="EmptyCell in a table (mobile=&quot;cards&quot; below 640px)" className="w-full">
        <div className="w-full">
          <DataTable mobile="cards" caption="Applications" columns={COLUMNS} rows={SAMPLE_APPS.slice(0, 4)} getRowId={(app) => app.domain} />
        </div>
      </Item>
    </Section>
  );
}

function Surfaces() {
  return (
    <Section
      id="card-kit"
      title="Card, subsection and field action"
      description="Card is every panel: md (20px) for a panel, sm (16px) in a grid or list, none for rows that draw their own edges. Subsection titles a part of a section or card. A button that acts on a field sits beside the control, aligned with it."
    >
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Resources" description="What this app may use of the server.">
          <p className="text-13 text-fg-muted">padding md, 20px</p>
        </Card>
        <Card padding="sm" title="Backups" description="Last 24 hours">
          <p className="text-13 text-fg-muted">padding sm, 16px</p>
        </Card>
        <Card padding="none" title="Recent pushes">
          <ul className="divide-y divide-border text-13">
            <li className="px-5 py-2.5">main · 4f1c2a0</li>
            <li className="px-5 py-2.5">main · 9b3e771</li>
          </ul>
        </Card>
      </div>
      <Card title="Deploy on push" description="Pushes to main deploy this app." footer={<Button variant="primary">Save</Button>}>
        <div className="flex flex-col gap-6">
          <Subsection title="Repository" description="Where the code comes from.">
            <Field label="Repository URL" action={<Button icon={<GitBranch />}>Inspect</Button>} description="HTTPS or SSH.">
              <Input mono defaultValue="https://github.com/acme/shop" />
            </Field>
          </Subsection>
          <Subsection title="Meters with a level" description="Amber at 75%, red at 90%: the glyph and the word come with the colour.">
            <div className="grid gap-4 sm:grid-cols-3">
              <Meter label="CPU" value={23} />
              <Meter label="Memory" value={81} />
              <Meter label="Disk" value={94} />
            </div>
          </Subsection>
        </div>
      </Card>
    </Section>
  );
}

function Friction() {
  return (
    <Section
      id="friction"
      title="Confirmation friction"
      description="How much is asked is proportional to what can be lost. none: reversible, it just runs. simple: one resource that can be made again, one question, focus on Cancel. type: data lost for good or a wide reach, type the name. On a fleet, the dialog names the server."
    >
      <Stage>
        <Row>
          <Item label="friction=&quot;none&quot;">
            <ConfirmDialog
              friction="none"
              title="Disable nightly-report"
              description="It can be enabled again."
              actionLabel="Disable"
              onConfirm={() => Promise.resolve()}
              trigger={<Button>Disable job</Button>}
            />
          </Item>
          <Item label="friction=&quot;simple&quot;">
            <ConfirmDialog
              friction="simple"
              server="web-2"
              title="Stop shop.example.com"
              description="It stops answering until you start it again. Nothing is deleted."
              actionLabel="Stop application"
              onConfirm={() => Promise.resolve()}
              trigger={<Button data-testid="open-confirm-simple">Stop</Button>}
            />
          </Item>
          <Item label="friction=&quot;type&quot;">
            <ConfirmDialog
              title="Delete shop.example.com"
              description="Stops the service and removes its files and site. Backups are kept."
              confirmText="shop.example.com"
              actionLabel="Delete application"
              onConfirm={() => Promise.resolve()}
              trigger={<Button variant="danger">Delete</Button>}
            />
          </Item>
        </Row>
      </Stage>
    </Section>
  );
}

function Headers() {
  return (
    <Section
      id="page-header"
      title="Page header"
      description="Breadcrumbs, the title (mono for a system identifier) with its state, one line of facts, one sentence; then the actions: secondary, the one primary, and everything else behind More actions. On a fleet, the server is named above the title."
    >
      <Stage plain>
        <PageHeader
          flush
          server="web-2"
          title="shop.example.com"
          mono
          breadcrumbs={[{ label: "Applications", to: "/__design" }]}
          status={<StatusPill state="running" />}
          meta={
            <>
              <span>Next.js</span>
              <span className="mono">:3001</span>
              <span className="mono">shop.example.com</span>
            </>
          }
          secondaryActions={<Button icon={<RotateCw />}>Restart</Button>}
          primaryAction={<Button variant="primary">Update</Button>}
          overflow={
            <>
              <MenuItem icon={<DatabaseBackup />}>Back up now</MenuItem>
              <MenuSeparator />
              <MenuItem destructive>Stop</MenuItem>
            </>
          }
        />
      </Stage>
    </Section>
  );
}

/** Specimens of the components added in 3.1. */
export function Kit() {
  return (
    <>
      <Headers />
      <Notices />
      <Jobs />
      <Filters />
      <Empties />
      <Surfaces />
      <Friction />
    </>
  );
}
