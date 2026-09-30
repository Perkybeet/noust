import { Boxes, Globe, Plus, Upload } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { LinkTabs } from "../app/LinkTabs";
import { APP_TABS } from "../app/nav";
import {
  AuthLayout,
  CommandHint,
  DashboardPage,
  DetailPage,
  FileEditorPage,
  FilterBar,
  JobProgress,
  KeyValueList,
  ListPage,
  SaveBar,
  SegmentedControl,
  SettingsLayout,
  StatTile,
  Stepper,
  Subsection,
  Wizard,
  WizardActions,
} from "../components/page";
import {
  Button,
  Card,
  Chart,
  DataTable,
  Dialog,
  EmptyState,
  Field,
  Input,
  MenuItem,
  Notice,
  StatusPill,
  Switch,
  Textarea,
} from "../components/ui";
import type { Column } from "../components/ui";
import { Section } from "./gallery";
import type { SampleApp } from "./sample";
import { SAMPLE_APPS, ago, sampleMetrics } from "./sample";

/** A template drawn in a frame the size of a page, with its number and name. */
function Frame({ id, name, children }: { id: string; name: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="mb-2 text-12 font-medium text-fg-faint">{name}</p>
      <div data-template-demo={id} className="min-w-0 overflow-hidden rounded-card border border-border bg-bg p-8 max-sm:p-4">
        {children}
      </div>
    </div>
  );
}

const APP_COLUMNS: Column<SampleApp>[] = [
  { id: "domain", header: "Application", cell: (app) => app.domain, sortValue: (app) => app.domain },
  { id: "state", header: "State", cell: (app) => <StatusPill state={app.status} appearance="inline" size="sm" />, card: "status", width: "w-32" },
  { id: "type", header: "Type", cell: (app) => app.type },
  { id: "deployed", header: "Deployed", cell: (app) => ago(app.deployedMinutesAgo) },
  { id: "port", header: "Port", cell: (app) => (app.port === null ? "–" : `:${String(app.port)}`), align: "end", mono: true },
];

function ListDemo() {
  const [q, setQ] = useState("");
  const rows = SAMPLE_APPS.filter((app) => app.domain.includes(q));
  return (
    <ListPage
      header={{
        title: "Applications",
        description: "Everything this server runs, and the state it is in.",
        primaryAction: (
          <Button variant="primary" icon={<Plus />}>
            New application
          </Button>
        ),
        overflow: <MenuItem icon={<Upload />}>Import an application</MenuItem>,
      }}
      filters={
        <FilterBar
          label="Filter applications"
          search={{ value: q, onChange: setQ, label: "Search applications", placeholder: "Name or domain" }}
          count={`${String(rows.length)} of ${String(SAMPLE_APPS.length)} applications`}
        />
      }
      footer={<CommandHint command="noust list" label="From a terminal" />}
    >
      <DataTable
        mobile="cards"
        caption="Applications"
        columns={APP_COLUMNS}
        rows={rows}
        getRowId={(app) => app.domain}
        onRowActivate={() => undefined}
        empty={<EmptyState variant="inline" title={`No applications match “${q}”.`} action={<Button size="sm" variant="ghost" onClick={() => setQ("")}>Clear filters</Button>} />}
      />
    </ListPage>
  );
}

function DetailDemo() {
  return (
    <DetailPage
      header={{
        title: "worker.example.dev",
        mono: true,
        breadcrumbs: [{ label: "Applications", to: "/__design" }],
        status: <StatusPill state="failed" />,
        meta: (
          <>
            <span>Node.js</span>
            <span className="mono">:3011</span>
          </>
        ),
        secondaryActions: <Button>Restart</Button>,
        primaryAction: <Button variant="primary">Update</Button>,
        overflow: <MenuItem destructive>Stop</MenuItem>,
      }}
      banner={
        <Notice tone="error" variant="banner" title="The service keeps failing to start" action={<Button size="sm">Diagnose</Button>}>
          systemd gave up after 5 restarts in 10 seconds. The last version that answered is still on disk.
        </Notice>
      }
      job={<JobProgress state="running" title="Updating worker.example.dev" step="npm ci" announce={false} />}
      tabs={<LinkTabs label="Application sections" tabs={APP_TABS.map((tab) => ({ ...tab, params: { domain: "worker.example.dev" } }))} />}
    >
      <KeyValueList
        items={[
          { label: "How it runs", value: "A service, restarted if it stops" },
          { label: "Directory", value: "/var/www/apps/worker-example-dev", mono: true, copy: "/var/www/apps/worker-example-dev" },
        ]}
      />
    </DetailPage>
  );
}

const SETTINGS_ITEMS = [
  { to: "/__design", label: "General", exact: true },
  { to: "/__design/deploys", label: "Deploys" },
  { to: "/__design/deploy-on-push", label: "Deploy on push" },
  { to: "/__design/resources", label: "Resources" },
  { to: "/__design/export", label: "Export" },
  { to: "/__design/delete", label: "Delete", danger: true },
];

function SettingsDemo() {
  const [path, setPath] = useState("/health");
  const saved = "/health";
  const changes = path === saved ? 0 : 1;
  return (
    <SettingsLayout label="Application settings" items={SETTINGS_ITEMS}>
      <Card title="Startup check" description="Before switching traffic, Noust requests this path and waits for an answer.">
        <div className="flex flex-col gap-5">
          <Field label="Path" description="Answered with any status below 500.">
            <Input mono value={path} onValueChange={(value: string) => setPath(value)} />
          </Field>
          <Field label="Instant rollback" nativeLabel={false} description="Each deploy is kept apart; going back takes seconds.">
            <Switch aria-label="Instant rollback" defaultChecked />
          </Field>
        </div>
      </Card>
      <SaveBar changes={changes} onDiscard={() => setPath(saved)} onSave={() => undefined} />
    </SettingsLayout>
  );
}

function DashboardDemo() {
  const [range, setRange] = useState<"1h" | "24h" | "7d">("1h");
  const metrics = sampleMetrics();
  const figures: [string, string, string][] = [
    ["Applications", "14 of 17", "running"],
    ["Failed", "2", "worker, labs"],
    ["Deploys today", "8", "1 failed"],
    ["Certificates", "1 expiring", "in 12 days"],
    ["Backups, 24 h", "5 of 17", "apps covered"],
    ["Disk", "61%", "of 480 GB"],
  ];
  return (
    <DashboardPage
      header={{
        title: "Overview",
        secondaryActions: (
          <SegmentedControl
            label="Time range"
            value={range}
            onValueChange={setRange}
            options={[
              { value: "1h", label: "1h" },
              { value: "24h", label: "24h" },
              { value: "7d", label: "7d" },
            ]}
          />
        ),
        primaryAction: (
          <Button variant="primary" icon={<Plus />}>
            New application
          </Button>
        ),
      }}
      figures={figures.map(([label, value, detail]) => (
        <StatTile key={label} label={label} value={value} detail={detail} />
      ))}
      attention={
        <Card title="Needs attention" padding="none">
          <ul className="divide-y divide-border text-13">
            <li className="flex items-center justify-between gap-3 px-5 py-2.5">
              <span className="flex min-w-0 items-center gap-2">
                <StatusPill state="failed" appearance="inline" size="sm" />
                <span className="mono truncate">worker.example.dev</span>
              </span>
              <Button size="sm">View</Button>
            </li>
            <li className="flex items-center justify-between gap-3 px-5 py-2.5">
              <span className="flex min-w-0 items-center gap-2">
                <StatusPill state="warning" appearance="inline" size="sm" label="Expiring" />
                <span className="mono truncate">example.com</span>
              </span>
              <Button size="sm">Renew</Button>
            </li>
          </ul>
        </Card>
      }
      activity={
        <Card title="Recent activity" padding="none">
          <ul className="divide-y divide-border text-13">
            <li className="flex items-center gap-2 px-5 py-2.5">
              <StatusPill state="running" appearance="inline" size="sm" label="Deployed" />
              <span className="mono truncate">shop.example.dev #13</span>
              <span className="ml-auto text-12 text-fg-faint">12 min ago</span>
            </li>
            <li className="flex items-center gap-2 px-5 py-2.5">
              <StatusPill state="failed" appearance="inline" size="sm" label="Failed" />
              <span className="mono truncate">worker.example.dev #7</span>
              <span className="ml-auto text-12 text-fg-faint">45 min ago</span>
            </li>
          </ul>
        </Card>
      }
      charts={["CPU", "Memory"].map((title) => (
        <Card key={title} padding="sm" className="sm:col-span-2">
          <Chart
            title={title}
            description="Last 30 minutes"
            timestamps={metrics.timestamps}
            series={[{ label: "This server", values: title === "CPU" ? metrics.cpu : metrics.memory }]}
            formatValue={(v) => (title === "CPU" ? `${v.toFixed(0)}%` : `${v.toFixed(0)} MB`)}
          />
        </Card>
      ))}
    />
  );
}

const STEPS = [
  { id: "source", label: "Source" },
  { id: "address", label: "Address" },
  { id: "configure", label: "Configure" },
  { id: "variables", label: "Variables" },
  { id: "deploy", label: "Deploy" },
];

function WizardDemo() {
  const [domain, setDomain] = useState("");
  const [dialog, setDialog] = useState(false);
  return (
    <div className="flex flex-col gap-6">
      <Wizard
        steps={STEPS}
        current="address"
        onSelectStep={() => undefined}
        title="Address"
        description="The domain the application answers on. Point its DNS at this server first."
        summary={
          <Card padding="sm" title="So far">
            <KeyValueList items={[{ label: "Source", value: "github.com/acme/shop", mono: true }]} />
          </Card>
        }
        actions={{
          back: { onClick: () => undefined },
          next: { onClick: () => undefined },
          ...(domain.trim() === "" ? { missing: "Enter the domain the application answers on." } : {}),
        }}
      >
        <Field label="Domain">
          <Input mono placeholder="shop.example.com" value={domain} onValueChange={(value: string) => setDomain(value)} />
        </Field>
      </Wizard>
      <div>
        <Button onClick={() => setDialog(true)}>Open the dialog form of a wizard</Button>
        <Dialog
          open={dialog}
          onOpenChange={setDialog}
          size="lg"
          title="Add a server"
          footer={<WizardActions back={{ onClick: () => undefined }} next={{ onClick: () => setDialog(false) }} className="w-full" />}
        >
          <div className="flex flex-col gap-6">
            <Stepper
              orientation="horizontal"
              current="join"
              steps={[
                { id: "authorize", label: "Authorize" },
                { id: "join", label: "Join" },
                { id: "done", label: "Done" },
              ]}
            />
            <Subsection title="Paste the join code" description="Printed on the OTHER server, the one you are adding.">
              <Input mono aria-label="Join code" placeholder="noust-join:v1:…" />
            </Subsection>
          </div>
        </Dialog>
      </div>
    </div>
  );
}

const SITE = `server {
    listen 443 ssl;
    server_name example.net www.example.net;

    location / {
        proxy_pass http://127.0.0.1:3004;
    }
}
`;

function EditorDemo() {
  const [text, setText] = useState(SITE);
  return (
    <FileEditorPage
      header={{
        title: "example.net",
        mono: true,
        breadcrumbs: [{ label: "Domains", to: "/__design" }],
        status: <StatusPill state="running" label="Serving" />,
        secondaryActions: <Button>Disable</Button>,
        overflow: <MenuItem destructive>Delete site</MenuItem>,
      }}
      notice={<Notice title="Tested with nginx -t before it is saved" />}
      meta={
        <>
          <span className="flex items-center gap-1">
            <Globe aria-hidden="true" className="size-icon-sm" />
            example.net, www.example.net
          </span>
          <span className="mono">/etc/nginx/sites-available/example.net</span>
        </>
      }
      bar={{ changes: text === SITE ? 0 : 1, onDiscard: () => setText(SITE), onTest: () => undefined, onTestAndSave: () => undefined }}
    >
      <Textarea aria-label="Site configuration" mono value={text} onChange={(event) => setText(event.target.value)} className="h-full" />
    </FileEditorPage>
  );
}

function AuthDemo() {
  return (
    <AuthLayout as="div" title="Sign in" description="Use an access token, or a passkey." host="web-1.example.com" subtitle="Noust console 3.1.0" footer={<CommandHint command="noust web token" label="Lost the token?" />}>
      <div className="flex flex-col gap-5">
        <Field label="Access token">
          <Input type="password" mono autoComplete="current-password" />
        </Field>
        <Button variant="primary" size="lg" className="w-full">
          Sign in
        </Button>
      </div>
    </AuthLayout>
  );
}

/** The seven page templates, live, as a page composes them. */
export function Templates() {
  return (
    <>
      <Section
        id="t1"
        title="T1 List"
        description="Header with the create action, optional subset tabs, filters, the table, the CLI hint at the foot. Under 640px the rows become cards with their actions in view. What configures the list goes to a tab or to settings; a row's light detail opens in a drawer."
      >
        <Frame id="list" name="ListPage">
          <ListDemo />
        </Frame>
      </Section>
      <Section
        id="t2"
        title="T2 Detail with tabs"
        description="A stable header with the state, the facts and the actions; a status banner between the header and the tabs when the resource is broken, on every tab; the job in hand; the tabs as URLs."
      >
        <Frame id="detail" name="DetailPage">
          <DetailDemo />
        </Frame>
      </Section>
      <Section
        id="t3"
        title="T3 Settings with side navigation"
        description="A 200px list of subsections, each its own URL, beside content of at most 880px; one form per subsection, saved from the bar at its foot; the destructive subsection last, apart. On a phone the list is its own index page."
      >
        <Frame id="settings" name="SettingsLayout + SaveBar">
          <SettingsDemo />
        </Frame>
      </Section>
      <Section
        id="t4"
        title="T4 Dashboard"
        description="Key figures, then what needs attention beside recent activity, then the charts. It summarises and links; it never repeats a whole list. An empty server gets first steps instead."
      >
        <Frame id="dashboard" name="DashboardPage">
          <DashboardDemo />
        </Frame>
      </Section>
      <Section
        id="t5"
        title="T5 Wizard"
        description="One stepper: vertical beside a page's step, horizontal in a dialog. Continue is never disabled: when something is missing, pressing it says what."
      >
        <Frame id="wizard" name="Wizard, Stepper, WizardActions">
          <WizardDemo />
        </Frame>
      </Section>
      <Section
        id="t6"
        title="T6 File editor"
        description="The editor takes the screen's height; the bar at its foot says what changed and offers Test, and Test and save. No logs, no danger zone on this page."
      >
        <Frame id="file-editor" name="FileEditorPage">
          <EditorDemo />
        </Frame>
      </Section>
      <Section id="t7" title="T7 Access" description="Sign in, a sealed central, the legal notice: one column of 400px, the machine's name first, one field, one large button, the terminal's way under it.">
        <Frame id="auth" name="AuthLayout">
          <AuthDemo />
        </Frame>
      </Section>
      <Section id="t-empty" title="First use in a template" description="A list with nothing in it shows one first-use empty state in the table's place: no column headers, no filters.">
        <Frame id="list-empty" name="ListPage, empty">
          <ListPage header={{ title: "Backups", primaryAction: <Button variant="primary">Create backup</Button> }}>
            <EmptyState
              variant="firstUse"
              icon={<Boxes />}
              title="No backups yet"
              description="A backup copies an application's files, its .env and its databases."
              action={<Button>Create backup</Button>}
              command="noust backup create shop.example.com"
            />
          </ListPage>
        </Frame>
      </Section>
    </>
  );
}
