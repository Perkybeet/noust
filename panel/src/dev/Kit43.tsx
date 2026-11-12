import { useState } from "react";

import { FleetList, ScopeLine, ServersTable, WhichServerDialog } from "../components/page";
import type { FleetServer, ServersTableRow, WhichServerOption } from "../components/page";
import { Button, DataTable, Mono, StatusPill } from "../components/ui";
import type { Column, Status } from "../components/ui";
import { DoDont, Item, Row, Section, Stage } from "./gallery";
import { BothThemes } from "./gallery";

/** What a fleet of five is in the gallery: one that answers late, one whose Noust is older than the view, and the central. */
const HOUR = 3_600_000;
const CENTRAL_01: FleetServer = { name: "central-01", central: true };
const WEB_01: FleetServer = { name: "web-01" };
const WEB_02: FleetServer = { name: "web-02", answer: { kind: "stale", since: new Date(Date.now() - 2 * HOUR - 8 * 60_000) } };
const DB_02: FleetServer = { name: "db-02" };
const VPS_03: FleetServer = { name: "vps-03", answer: { kind: "unsupported", version: "4.2.1" } };
const SERVERS: readonly FleetServer[] = [CENTRAL_01, WEB_01, WEB_02, DB_02, VPS_03];

interface Backup {
  app: string;
  server: string;
  state: Status;
  stateWord: string;
  last: string;
  copies: number;
}

const BACKUPS: readonly Backup[] = [
  { app: "example.com", server: "web-01", state: "running", stateWord: "Up to date", last: "7 h ago", copies: 14 },
  { app: "shop.example.net", server: "web-01", state: "running", stateWord: "Up to date", last: "7 h ago", copies: 7 },
  { app: "catalogo.example.org", server: "web-01", state: "warning", stateWord: "No copies", last: "Never", copies: 0 },
  { app: "blog.example.org", server: "web-02", state: "running", stateWord: "Up to date", last: "1 d ago", copies: 7 },
  { app: "clientes.example.com", server: "db-02", state: "running", stateWord: "Up to date", last: "3 h ago", copies: 30 },
  { app: "lanzamiento.example.org", server: "central-01", state: "warning", stateWord: "No copies", last: "Never", copies: 0 },
];

const APP: Column<Backup> = { id: "app", header: "Application", card: "title", cell: (row) => <Mono tone="default">{row.app}</Mono>, sortValue: (row) => row.app };
const STATE: Column<Backup> = { id: "state", header: "State", card: "status", width: "w-36", cell: (row) => <StatusPill state={row.state} label={row.stateWord} appearance="inline" size="sm" /> };
const LAST: Column<Backup> = { id: "last", header: "Last backup", cell: (row) => row.last };
const COPIES: Column<Backup> = { id: "copies", header: "Copies", align: "end", mono: true, cell: (row) => row.copies, sortValue: (row) => row.copies };
const COLUMNS: readonly Column<Backup>[] = [APP, STATE, LAST, COPIES];
const SERVER: Column<Backup> = { id: "server", header: "Server", width: "w-32", cell: (row) => <Mono tone="default">{row.server}</Mono>, sortValue: (row) => row.server };

const counted = (shown: number, total: number): string => (shown === total ? `${String(total)} applications` : `${String(shown)} of ${String(total)} applications`);

function BackupsList({ scope }: { scope: "all" | "server" }) {
  const [text, setText] = useState("");
  const here = scope === "server" ? BACKUPS.filter((row) => row.server === "web-01") : BACKUPS;
  const rows = here.filter((row) => text === "" || row.app.includes(text));
  return (
    <FleetList<Backup>
      scope={scope}
      servers={scope === "server" ? [{ name: "web-01" }] : SERVERS}
      rows={rows}
      serverOf={(row) => row.server}
      columns={COLUMNS}
      getRowId={(row) => row.app}
      caption={scope === "all" ? "Backups of every server" : "Backups of web-01"}
      filterLabel="Filter backups"
      search={{ value: text, onChange: setText, label: "Search backups", placeholder: "Search" }}
      count={counted}
    />
  );
}

function Scope() {
  return (
    <Section
      id="k43-scope"
      title="Scope line"
      description="The first line of a page's body says whose it is, once and in the same place on every page: how many servers it speaks of, or which one, and, one line each, the servers that did not answer fresh and whole or whose Noust does not know the view. It never goes in the header (the title does not repeat the server) and it never has a colour of its own: the sentences stay in the stone's text and only the glyph of a state is coloured."
    >
      <BothThemes>
        <div className="flex flex-col gap-6">
          <Item label="All servers, one late and one older than the view">
            <ScopeLine scope={{ kind: "all", servers: SERVERS }} serverTarget={() => ({ to: "/__design" })} />
          </Item>
          <Item label="One server, with the way to see them all">
            <ScopeLine scope={{ kind: "server", server: { name: "web-01" } }} />
          </Item>
          <Item label="A library: the same on every server">
            <ScopeLine scope={{ kind: "library" }} />
          </Item>
          <Item label="A server that gave nothing">
            <ScopeLine scope={{ kind: "all", servers: [{ name: "web-01" }, { name: "nas-01", answer: { kind: "unreachable" } }] }} />
          </Item>
        </div>
      </BothThemes>
      <DoDont
        rule="The scope is a line of the body, in the stone's text"
        doThis={<ScopeLine scope={{ kind: "all", servers: SERVERS }} />}
        notThis={
          <div className="flex flex-col gap-2">
            <p className="title text-18 text-fg">Backups on web-01, web-02, db-02</p>
            <p className="text-13 text-warn">web-02 is not answering, vps-03 is out of date!</p>
          </div>
        }
        why="A header that repeats the server says it twice, and a sentence coloured as a warning spends the amber of a state on a fact that is already told by a glyph and a word. The line is calm when the servers answered."
      />
    </Section>
  );
}

function Fleet() {
  return (
    <Section
      id="k43-fleet-list"
      title="Fleet list"
      description="The table of always, for rows that live on several servers: with All servers the server is a column right after what each row is (it sorts) and a filter that counts what each server has; the rows of a server that did not answer say so, with how old they are. With one server the same list loses both and keeps everything else, so a page is one component for both scopes."
    >
      <BothThemes stacked>
        <div className="flex flex-col gap-8">
          <Item label="All servers">
            <div className="w-full">
              <BackupsList scope="all" />
            </div>
          </Item>
          <Item label="One server: neither the column nor the filter">
            <div className="w-full">
              <BackupsList scope="server" />
            </div>
          </Item>
        </div>
      </BothThemes>
      <DoDont
        rule="One table with the server as a column, not one table for each server"
        doThis={
          <DataTable caption="Backups of every server" columns={[APP, SERVER, LAST]} rows={BACKUPS.slice(0, 4)} getRowId={(row) => row.app} mobile="scroll" />
        }
        notThis={
          <div className="flex flex-col gap-3">
            {["web-01", "web-02"].map((server) => (
              <div key={server} className="flex flex-col gap-1">
                <p className="title text-14 text-fg">{server}</p>
                <DataTable caption={`Backups of ${server}`} columns={[APP, LAST]} rows={BACKUPS.filter((row) => row.server === server).slice(0, 2)} getRowId={(row) => row.app} mobile="scroll" />
              </div>
            ))}
          </div>
        }
        why="A table for each server repeats the headers, cannot be sorted or filtered across servers and grows with the fleet. The column sorts, the filter narrows, and the count says what each server has."
      />
    </Section>
  );
}

const FIREWALLS: readonly ServersTableRow[] = [
  { server: CENTRAL_01, summary: "Active, 7 rules", note: "Changed 3 d ago", state: { status: "running", label: "Filters" } },
  { server: WEB_01, summary: "Active, 9 rules", note: "Changed 12 d ago", state: { status: "running", label: "Filters" } },
  { server: WEB_02, summary: "Active, 9 rules", note: "Changed 40 d ago", state: { status: "running", label: "Filters" } },
  { server: DB_02, summary: "Active, 6 rules", note: "Only 5432 from web-01", state: { status: "running", label: "Filters" } },
  { server: VPS_03 },
];

function Servers() {
  return (
    <Section
      id="k43-servers-table"
      title="Table of servers"
      description="What a section shows with All servers when its content is each machine's own and cannot be put in one list (a firewall, the interfaces): one row for each server with a summary of what the section says of it, and the way in, Open on web-01, a link that chooses that server. Also the door of the tools of one machine (Files, Terminal): the server and the way in, nothing to compare. The links of the samples lead back to this gallery."
    >
      <BothThemes stacked>
        <div className="flex flex-col gap-8">
          <Item label="A summary of each machine's own">
            <div className="w-full">
              <ServersTable caption="Firewall of every server" summaryHeader="Firewall" rows={FIREWALLS} open={() => ({ to: "/__design" })} />
            </div>
          </Item>
          <Item label="A door: a tool of one machine">
            <div className="w-full">
              <ServersTable caption="Files of every server" rows={SERVERS.map((server) => ({ server }))} open={() => ({ to: "/__design" })} />
            </div>
          </Item>
        </div>
      </BothThemes>
    </Section>
  );
}

const CANDIDATES: readonly WhichServerOption[] = [
  { name: "central-01", central: true, detail: "Debian 12" },
  { name: "web-01", detail: "Ubuntu 24.04" },
  { name: "web-02", detail: "Ubuntu 22.04", answer: { kind: "stale", since: new Date(Date.now() - 2 * HOUR - 8 * 60_000) } },
  { name: "db-02", detail: "Debian 12" },
  { name: "vps-03", detail: "Ubuntu 20.04", unfit: "Docker is not installed" },
];

function Which() {
  const [shown, setShown] = useState<"many" | "one" | "none" | null>(null);
  const servers = shown === "one" ? CANDIDATES.map((server) => (server.name === "web-01" ? server : { ...server, unfit: "Docker is not installed" })) : shown === "none" ? CANDIDATES.map((server) => ({ ...server, unfit: "Docker is not installed" })) : CANDIDATES;
  return (
    <Section
      id="k43-which-server"
      title="On which server?"
      description="Asked when a creation starts with All servers chosen: the button is where it always is, and the selector stays as it was. Every server that is not a fit stays in the list, off, with its reason (no connection, no Docker, a Noust too old). None is preselected unless exactly one can take it, so an Enter cannot deploy to the wrong server; Continue with nothing chosen says what is missing and puts the keyboard on the choice."
    >
      <Stage>
        <Row>
          <Item label="Several fit, none chosen">
            <Button
              onClick={() => {
                setShown("many");
              }}
            >
              Use this recipe
            </Button>
          </Item>
          <Item label="Only one fits: it is chosen">
            <Button
              variant="secondary"
              onClick={() => {
                setShown("one");
              }}
            >
              Only web-01 fits
            </Button>
          </Item>
          <Item label="None fits: each says why">
            <Button
              variant="secondary"
              onClick={() => {
                setShown("none");
              }}
            >
              None fits
            </Button>
          </Item>
        </Row>
      </Stage>
      <WhichServerDialog
        open={shown !== null}
        onOpenChange={(open) => {
          if (!open) setShown(null);
        }}
        description="Choose where Uptime Kuma is installed."
        servers={servers}
        onChoose={() => undefined}
      />
    </Section>
  );
}

/** Components of 4.3: the pieces for "All servers" (item 196), which the pages of each area draw once the scope is a state. */
export function Kit43() {
  return (
    <>
      <Scope />
      <Fleet />
      <Servers />
      <Which />
    </>
  );
}
