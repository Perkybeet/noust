import { Copy } from "lucide-react";
import { useState } from "react";

import { LinkTabs } from "../app/LinkTabs";
import { APP_TABS } from "../app/nav";
import { PageHeader } from "../app/PageHeader";
import {
  DashboardPage,
  ErrorBlock,
  RowMenu,
  SaveBar,
  SettingsGroup,
  SettingsLayout,
  StatTile,
} from "../components/page";
import { LoadingRegion } from "../components/page/LoadingRegion";
import type { SettingsNavItem } from "../components/page";
import {
  Button,
  Card,
  Checkbox,
  ChoiceCards,
  ConfirmDialog,
  DataTable,
  DemotePrimary,
  Disclosure,
  EmptyState,
  FeatureState,
  FieldGroup,
  InlineMessage,
  MenuItem,
  MenuSeparator,
  Mono,
  Notice,
  RowList,
  Setting,
  SkeletonCard,
  SkeletonRows,
  SkeletonSection,
  SplitButton,
  StatusGlyph,
  StatusPill,
  Switch,
  TextLink,
} from "../components/ui";
import type { Column } from "../components/ui";
import { BothThemes, DoDont, Item, Row, Section, Stage } from "./gallery";

/** A switch that holds its own value, for a specimen. */
function useFlag(initial: boolean): [boolean, (next: boolean) => void] {
  return useState(initial);
}

/** The few seconds a change takes to apply, so a specimen can show its arc. */
function useSlowFlag(initial: boolean): {
  checked: boolean;
  busy: boolean;
  change: (next: boolean) => void;
} {
  const [checked, setChecked] = useState(initial);
  const [busy, setBusy] = useState(false);
  return {
    checked,
    busy,
    change: (next) => {
      setBusy(true);
      window.setTimeout(() => {
        setChecked(next);
        setBusy(false);
      }, 1200);
    },
  };
}

function Settings40() {
  const [notify, setNotify] = useFlag(true);
  const [digest, setDigest] = useFlag(false);
  const [zero, setZero] = useFlag(true);
  const [sandbox, setSandbox] = useFlag(true);
  const [backup, setBackup] = useFlag(true);
  const [ssh, setSsh] = useFlag(false);
  const slow = useSlowFlag(false);
  const [labelOn, setLabelOn] = useFlag(true);
  return (
    <Section
      id="k40-setting"
      title="Setting and SettingsGroup"
      description="A setting is a row: its name, one sentence of help, the control at the end. 44px without help, 60px with one line; nothing in a row is taller than its text. The state of a feature is a small pill in the header of its card; a risk is one line under the help; a tinted block never says that something is on. A switch applies the moment it is moved and never shares a surface with a Save button."
    >
      <Stage>
        <div
          className="grid gap-6 lg:grid-cols-2"
          data-gallery-specimen="setting-sizes"
        >
          <Item label="44px: a name and a switch" className="w-full">
            <div
              data-gallery-specimen="setting-plain"
              className="w-full rounded-card border border-border bg-surface"
            >
              <Setting
                label="Send notifications"
                checked={notify}
                onCheckedChange={setNotify}
              />
            </div>
          </Item>
          <Item
            label="60px: a name, one line of help and a switch"
            className="w-full"
          >
            <div
              data-gallery-specimen="setting-help"
              className="w-full rounded-card border border-border bg-surface"
            >
              <Setting
                label="Daily summary"
                help="Sends a copy to the team."
                checked={digest}
                onCheckedChange={setDigest}
              />
            </div>
          </Item>
        </div>
      </Stage>
      <BothThemes>
        <div className="flex flex-col gap-4">
          <SettingsGroup
            title="Notifications"
            status={<StatusPill state="running" label="On" size="sm" />}
            command="noust notify status"
          >
            <Setting
              label="Send notifications"
              help="Failed deploys and expiring certificates go to 2 channels."
              checked={notify}
              onCheckedChange={setNotify}
            />
            <Setting
              label="Zero-downtime deploys"
              help="The new version starts beside the old one."
              risk="Two copies of the app run at once for a few seconds."
              riskDetail="Does not tolerate a SQLite database both copies write to, a queue with a single consumer, or a scheduler inside the process."
              checked={zero}
              onCheckedChange={setZero}
            >
              <p className="text-13 text-fg-muted">
                Keep the old version for 30 seconds.
              </p>
            </Setting>
            <Setting
              label="Required backup"
              help="Updates wait for a backup of the database."
              risk={backup ? undefined : "Updates run without a backup first."}
              checked={backup}
              onCheckedChange={setBackup}
              confirmOff={{
                title: "Turn off the required backup?",
                description:
                  "Updates will run without taking a backup of the database first.",
                actionLabel: "Turn off the backup",
              }}
            />
            <Setting
              label="Allow SSH passwords"
              help="Signing in with a password stays possible."
              elevated
              checked={ssh}
              onCheckedChange={setSsh}
            />
            <Setting
              label="Build sandbox"
              help="Builds run apart from the server's own files."
              status={
                <StatusPill state="warning" label="On, not proven" size="sm" />
              }
              checked={sandbox}
              onCheckedChange={setSandbox}
            />
          </SettingsGroup>
          <SettingsGroup
            title="States of a row"
            description="What a row can say besides on and off."
          >
            <Setting
              label="Applying"
              help="Keeps its value and draws its arc."
              checked={slow.checked}
              busy={slow.busy}
              onCheckedChange={slow.change}
            />
            <Setting
              label="Zero-downtime deploys"
              disabled
              reason="Needs instant rollback first."
              checked={false}
              onCheckedChange={() => undefined}
            />
            <Setting
              label="Require two-factor sign-in"
              lockedBy="Set by the ENS profile."
              checked
              onCheckedChange={() => undefined}
            />
            <Setting
              label="Instant rollback"
              help="Going back takes seconds."
              action={<Button size="sm">Turn on…</Button>}
            />
          </SettingsGroup>
        </div>
      </BothThemes>
      <DoDont
        rule="A feature's state is a pill in its card's header, not a tinted block"
        doThis={
          <Card
            title="Instant rollback"
            status={<StatusPill state="running" label="On" size="sm" />}
            actions={<Button size="sm">Turn off…</Button>}
            padding="sm"
          >
            <p className="text-13 text-fg-muted">
              Each deploy is kept apart; going back takes seconds.
            </p>
          </Card>
        }
        notThis={
          <FeatureState
            state="on"
            title="Instant rollback is on"
            action={<Button size="sm">Turn off…</Button>}
          >
            Each deploy is kept apart; going back takes seconds.
          </FeatureState>
        }
        why="A block of 74 to 142px for a switch of 20: it spends the green of running on a ticked box and says three times what the title already says."
      />
      <DoDont
        rule="The name does not change with the state"
        doThis={
          <div className="rounded-card border border-border bg-surface">
            <Setting
              label="Send notifications"
              checked={labelOn}
              onCheckedChange={setLabelOn}
            />
          </div>
        }
        notThis={
          <div className="rounded-card border border-border bg-surface">
            <Setting
              label={labelOn ? "Notifications are on" : "Notifications are off"}
              checked={labelOn}
              onCheckedChange={setLabelOn}
            />
          </div>
        }
        why="The control already says the state. A label that flips has to be read again to find out what the switch does."
      />
      <DoDont
        rule="A risk is one line under the help, with the detail one press away"
        doThis={
          <div className="rounded-card border border-border bg-surface">
            <Setting
              label="Zero-downtime deploys"
              help="The new version starts beside the old one."
              risk="Two copies run at once for a few seconds."
              riskDetail="Does not tolerate a SQLite database both copies write to."
              checked
              onCheckedChange={() => undefined}
            />
          </div>
        }
        notThis={
          <Notice tone="warning" title="Two copies of the app run at once">
            While the new version starts, the old one keeps answering, so both
            are alive for a few seconds. An app that writes to a SQLite database
            both copies share, that has a queue with a single consumer or a
            scheduler inside the process will do things twice or fail.
          </Notice>
        }
        why="A notice states a condition that is true now; a hypothetical risk is a line next to the control it concerns."
      />
      <DoDont
        rule="A switch that cannot be pressed says why"
        doThis={
          <div className="rounded-card border border-border bg-surface">
            <Setting
              label="Zero-downtime deploys"
              disabled
              reason="Needs instant rollback first."
              checked={false}
              onCheckedChange={() => undefined}
            />
          </div>
        }
        notThis={
          <div className="rounded-card border border-border bg-surface px-5 py-3">
            <Switch aria-label="Zero-downtime deploys" disabled />
          </div>
        }
        why="A dimmed control with no reason is a dead end. The reason is in the help, with a link to what is missing."
      />
    </Section>
  );
}

function InlineMessages40() {
  return (
    <Section
      id="k40-inline"
      title="InlineMessage"
      description="One line, no frame, no ground: the risk of an option, the state of a field or a row, a result that needs no more. The severity is the colour of a 12px glyph and a word for screen readers; the sentence is in the text colour. A framed condition with a fix is a Notice; a field's own validation stays its error."
    >
      <BothThemes>
        <div className="flex flex-col gap-3">
          <InlineMessage
            tone="warning"
            trailing={<TextLink to="/apps">What changes</TextLink>}
          >
            Two copies of the app run at once for a few seconds.
          </InlineMessage>
          <InlineMessage tone="error">
            Could not reach the host on port 22.
          </InlineMessage>
          <InlineMessage tone="success">Saved to the repository.</InlineMessage>
          <InlineMessage tone="info">
            Changes apply to the next deploy.
          </InlineMessage>
        </div>
      </BothThemes>
      <DoDont
        rule="A line is a line, not a framed notice"
        doThis={
          <InlineMessage tone="success">Saved to the repository.</InlineMessage>
        }
        notThis={<Notice tone="success" title="Saved to the repository" />}
        why="A notice is a framed condition with a fix, and it is one per section. A result that needs no more is a line, or nothing: the row that appears already says it."
      />
    </Section>
  );
}

function Leave40() {
  const [changes, setChanges] = useState(0);
  return (
    <Section
      id="k40-leave"
      title="Leaving with unsaved changes"
      description="A form with unsaved changes asks once before it is left: 'Discard unsaved changes?', with the focus on keeping the edits so Enter never loses anything. The save bar places the question, so no page writes its own. While the bar holds changes the header's primary steps aside and Save is the view's one primary: the header keeps its label and its size, and only the colour changes."
    >
      <Stage>
        <div
          className="flex flex-col gap-4"
          data-gallery-specimen="primary-yield"
        >
          <DemotePrimary active={changes > 0}>
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-card border border-border bg-surface px-5 py-3">
              <Mono>shop.example.com</Mono>
              <div className="flex items-center gap-2">
                <Button>Restart</Button>
                <Button variant="primary">Update</Button>
              </div>
            </div>
          </DemotePrimary>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setChanges((n) => n + 1)}>
              Edit a field
            </Button>
          </div>
          {/* A bar sticks to the bottom of its scroll box; in a box of its own it stays where the specimen is. */}
          <div className="overflow-y-auto">
            <SaveBar
              changes={changes}
              onDiscard={() => setChanges(0)}
              onSave={() => setChanges(0)}
              guard={false}
              yieldPrimary={false}
            />
          </div>
        </div>
      </Stage>
      <Stage>
        <ConfirmDialog
          friction="simple"
          title="Discard unsaved changes?"
          description="Leaving this page discards 3 unsaved changes."
          actionLabel="Discard changes"
          cancelLabel="Keep editing"
          onConfirm={() => Promise.resolve()}
          trigger={<Button>Show the question</Button>}
        />
      </Stage>
      <DoDont
        rule="One primary in the view, even while the form is dirty"
        doThis={
          <DemotePrimary active>
            <div className="flex items-center gap-2">
              <Button>Update</Button>
              <Button variant="primary">Save</Button>
            </div>
          </DemotePrimary>
        }
        notThis={
          <div className="flex items-center gap-2">
            <Button variant="primary">Update</Button>
            <Button variant="primary">Save</Button>
          </div>
        }
        why="Two violet buttons ask the operator to choose between the page's purpose and the form in front of them. The header's yields while there is something to save."
      />
    </Section>
  );
}

function Confirm40() {
  const refuse = (): Promise<void> =>
    Promise.reject(
      Object.assign(new Error("denied"), {
        hint: "Check the unit, then try again.",
        detail:
          "Job for ufw.service failed because the control process exited with error code.",
      }),
    );
  const plan = (
    <ul className="mono flex flex-col gap-1 text-12 text-fg-muted">
      <li>22/tcp ssh</li>
      <li>80/tcp http</li>
      <li>443/tcp https</li>
    </ul>
  );
  return (
    <Section
      id="k40-confirm"
      title="One confirmation"
      description="ConfirmDialog is the only confirmation: friction by what can be lost (none, simple, type). A plan with a before and after is its children and a size, not a second dialog; the failure of the action is drawn like every other, with the fix above the system's words, and the dialog stays open."
    >
      <Stage>
        <Row>
          {(["sm", "md", "lg"] as const).map((size) => (
            <Item key={size} label={`size="${size}"`}>
              <ConfirmDialog
                friction="simple"
                size={size}
                title="Turn on the firewall"
                description="Only the ports below stay open."
                actionLabel="Turn on the firewall"
                destructive={false}
                onConfirm={() => Promise.resolve()}
                trigger={<Button>{`Plan, ${size}`}</Button>}
              >
                {plan}
              </ConfirmDialog>
            </Item>
          ))}
          <Item label="The action fails: the dialog stays open and says why">
            <ConfirmDialog
              friction="simple"
              title="Turn on the firewall"
              description="Only the ports below stay open."
              actionLabel="Turn on the firewall"
              destructive={false}
              onConfirm={refuse}
              trigger={<Button>Fails</Button>}
            />
          </Item>
          <Item label="Typing the host name">
            <ConfirmDialog
              friction="type"
              confirmText="web-2"
              title="Restart web-2"
              description="Every application on it stops for about a minute."
              actionLabel="Restart server"
              onConfirm={() => Promise.resolve()}
              trigger={<Button>Restart…</Button>}
            />
          </Item>
        </Row>
      </Stage>
    </Section>
  );
}

function Split40() {
  const [loading, setLoading] = useState(false);
  const menu = (
    <>
      <MenuItem>Update without cache</MenuItem>
      <MenuItem>Build only</MenuItem>
    </>
  );
  return (
    <Section
      id="k40-split"
      title="Split button"
      description="One action with its variations: the main part does the usual thing, the arrow opens the same action done another way. One control of one height; each part is a button with a name of its own, and the focus ring goes round the pair so it is never cut where they meet."
    >
      <BothThemes>
        <div className="flex flex-col gap-5">
          <Row>
            <Item label="primary">
              <SplitButton name="Update" variant="primary" menu={menu}>
                Update
              </SplitButton>
            </Item>
            <Item label="secondary">
              <SplitButton
                name="Save and restart"
                menu={<MenuItem>Save only</MenuItem>}
              >
                Save and restart
              </SplitButton>
            </Item>
            <Item label="running: the arc is in the main part">
              <SplitButton
                name="Update"
                variant="primary"
                menu={menu}
                loading={loading}
                onClick={() => {
                  setLoading(true);
                  window.setTimeout(() => setLoading(false), 1800);
                }}
              >
                Update
              </SplitButton>
            </Item>
            <Item label="disabled">
              <SplitButton
                name="Retry"
                disabled
                menu={<MenuItem>Retry without cache</MenuItem>}
              >
                Retry
              </SplitButton>
            </Item>
          </Row>
          <Row>
            <Item label="size sm">
              <SplitButton
                name="Update"
                size="sm"
                variant="primary"
                menu={menu}
              >
                Update
              </SplitButton>
            </Item>
            <Item label="size lg">
              <SplitButton
                name="Deploy"
                size="lg"
                variant="primary"
                menu={menu}
              >
                Deploy
              </SplitButton>
            </Item>
          </Row>
        </div>
      </BothThemes>
      <DoDont
        rule="An action and its variations are one control"
        doThis={
          <SplitButton name="Update" variant="primary" menu={menu}>
            Update
          </SplitButton>
        }
        notThis={
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary">Update</Button>
            <Button variant="primary">Update without cache</Button>
            <Button variant="primary">Build only</Button>
          </div>
        }
        why="Three buttons for one verb say that the variations are as likely as the usual thing, and make three primaries in a view that has one."
      />
    </Section>
  );
}

interface Variable {
  name: string;
  state: "running" | "stopped";
}

const VARIABLES: Variable[] = [
  { name: "DATABASE_URL", state: "running" },
  { name: "SECRET_KEY", state: "running" },
  { name: "SENTRY_DSN", state: "stopped" },
];

function Rows40() {
  return (
    <Section
      id="k40-rows"
      title="Row list, row menu and row link"
      description="Rows of like things that are not a table: one bordered list with the card's radius, 44px at least, something at the start and a menu at the end. The menu is always named 'Actions for {name}' (one phrase in each language); the name of a row that opens it is a link in the text colour. A loading list is the skeleton of the same frame, an empty one an inline empty state, a failed one an error block."
    >
      <BothThemes>
        <div className="flex flex-col gap-5">
          <RowList aria-label="Environment variables (specimen)">
            {VARIABLES.map((variable) => (
              <RowList.Item
                key={variable.name}
                leading={<StatusGlyph state={variable.state} />}
                actions={
                  <RowMenu name={variable.name}>
                    <MenuItem>Edit</MenuItem>
                    <MenuItem icon={<Copy />}>Copy name</MenuItem>
                    <MenuSeparator />
                    <MenuItem destructive>Delete</MenuItem>
                  </RowMenu>
                }
              >
                <TextLink to="/apps" variant="row" size="ui" translate="no">
                  {variable.name}
                </TextLink>
              </RowList.Item>
            ))}
          </RowList>
          <LoadingRegion label="Loading variables">
            <SkeletonRows rows={3} />
          </LoadingRegion>
          <EmptyState
            variant="inline"
            title="No variables yet."
            action={<Button size="sm">Add a variable</Button>}
          />
          <ErrorBlock
            compact
            title="Could not load variables"
            error={Object.assign(new Error("x"), {
              hint: "Check that the application's directory exists.",
              detail:
                "ENOENT: no such file or directory, open '/var/www/apps/shop/shared/.env'",
            })}
          />
        </div>
      </BothThemes>
      <DoDont
        rule="A list of rows has one radius and one hairline"
        doThis={
          <RowList aria-label="Mounts (specimen)">
            <RowList.Item>/var/www/apps/shop/uploads</RowList.Item>
            <RowList.Item>/var/www/apps/shop/cache</RowList.Item>
          </RowList>
        }
        notThis={
          <ul className="divide-y divide-border rounded-control border border-border">
            <li className="px-3 py-1.5 text-13">/var/www/apps/shop/uploads</li>
            <li className="px-3 py-1.5 text-13">/var/www/apps/shop/cache</li>
          </ul>
        }
        why="Ninety-one lists written by hand have two radii, three paddings and rows from 28 to 52px. The list is one component."
      />
    </Section>
  );
}

function Fold40() {
  return (
    <Section
      id="k40-fold"
      title="Disclosure, as a card"
      description="What most operators skip, folded in a bordered surface: a button that says whether it is open, with one line of summary that says what is inside without opening it. Open from the start when what it holds already differs from the defaults. Never a details element: one implementation of folded."
    >
      <BothThemes>
        <div className="flex flex-col gap-3">
          <Disclosure
            variant="card"
            label="Advanced options"
            summary="Using the defaults"
          >
            <p className="text-13 text-fg-muted">
              Keep the old version for 30 seconds.
            </p>
          </Disclosure>
          <Disclosure
            variant="card"
            label="Advanced options"
            summary="3 of 12 changed"
            defaultOpen
          >
            <p className="text-13 text-fg-muted">
              Keep the old version for 30 seconds.
            </p>
          </Disclosure>
        </div>
      </BothThemes>
      <DoDont
        rule="Folded is a button with aria-expanded"
        doThis={
          <Disclosure
            variant="card"
            label="Advanced options"
            summary="Using the defaults"
          >
            <p className="text-13 text-fg-muted">Body</p>
          </Disclosure>
        }
        notThis={
          <details className="rounded-card border border-border px-3 py-2 text-13">
            <summary>Advanced options</summary>
            <p className="pt-2 text-fg-muted">Body</p>
          </details>
        }
        why="Ten bordered details elements drew their own chevron, their own padding and their own focus ring. The fold is one component."
      />
    </Section>
  );
}

function Groups40() {
  const [via, setVia] = useState<"releases" | "inplace">("releases");
  return (
    <Section
      id="k40-group"
      title="Field group and choice error"
      description="Related controls under one name and one message: a fieldset with a legend, and the error a Field shows (a glyph and a sentence in the failure colour) tied to the group. ChoiceCards takes the same error."
    >
      <BothThemes>
        <div className="flex flex-col gap-5">
          <FieldGroup
            legend="Notify by"
            description="Email, Telegram or a webhook."
            error="Choose at least one channel."
          >
            <Checkbox label="Email" />
            <Checkbox label="Telegram" />
          </FieldGroup>
          <FieldGroup legend="Also delete" optional>
            <Checkbox label="Its files" />
            <Checkbox label="Its certificate" />
          </FieldGroup>
          <ChoiceCards
            legend="How deploys work"
            value={via}
            onValueChange={setVia}
            error="Choose how deploys work."
            options={[
              {
                value: "releases",
                label: "Instant rollback",
                description:
                  "Each deploy is kept apart; going back takes seconds.",
              },
              {
                value: "inplace",
                label: "Single folder",
                description: "Every deploy rebuilds the same folder.",
              },
            ]}
          />
        </div>
      </BothThemes>
      <DoDont
        rule="A group's error is the field's error"
        doThis={
          <FieldGroup legend="Notify by" error="Choose at least one channel.">
            <Checkbox label="Email" />
          </FieldGroup>
        }
        notThis={
          <div className="flex flex-col gap-2">
            <p className="text-13 font-medium text-fg">Notify by</p>
            <Checkbox label="Email" />
            {/* design-exception: dead-class the specimen of the mistake: a class that names no colour is dropped without a word */}
            <p className="text-13 text-danger">Choose at least one channel.</p>
          </div>
        }
        why="Twenty-one errors written by hand have no glyph and no tie to their group, and five use a class that does not exist: Tailwind drops it silently and the message is painted without red."
      />
    </Section>
  );
}

function Skeletons40() {
  return (
    <Section
      id="k40-skeleton"
      title="Skeletons with the shape of the content"
      description="A placeholder has the frame, the padding and the row height of what it stands for, so nothing moves when it arrives. The region around it says what is loading; the placeholder only has a shape."
    >
      <BothThemes>
        <div className="flex flex-col gap-5">
          <LoadingRegion label="Loading variables">
            <SkeletonSection rows={2} />
          </LoadingRegion>
          <LoadingRegion label="Loading notifications">
            <SkeletonCard lines={2} />
          </LoadingRegion>
        </div>
      </BothThemes>
      <DoDont
        rule="The placeholder is shaped like the thing"
        doThis={
          <LoadingRegion label="Loading variables">
            <SkeletonRows rows={3} />
          </LoadingRegion>
        }
        notThis={
          <LoadingRegion label="Loading variables">
            <div className="h-24 w-full animate-breathe rounded-card bg-surface-active" />
          </LoadingRegion>
        }
        why="A rectangle of a height guessed by eye is replaced by rows of another height: the page jumps when the answer arrives."
      />
    </Section>
  );
}

function Place40() {
  return (
    <Section
      id="k40-place"
      title="Place line and status chips"
      description="Where a resource lives, above its title, when there is more than one place to tell apart: the server's name when the console manages several, then team, project and environment, each step a link to the list filtered to it. What else belongs on the state's line (3 not applied, deploys paused) comes after the state, as a chip that opens the thing it counts."
    >
      <BothThemes>
        <PageHeader
          title="shop.example.com"
          mono
          flush
          server="fra-1"
          place={[
            { label: "Acme", to: "/apps", search: { team: "acme" } },
            {
              label: "Web",
              to: "/apps",
              search: { team: "acme", project: "web" },
            },
            {
              label: "production",
              to: "/apps",
              search: {
                team: "acme",
                project: "web",
                environment: "production",
              },
            },
          ]}
          status={<StatusPill state="running" />}
          statusExtras={
            <Button size="sm" variant="secondary">
              3 not applied
            </Button>
          }
          meta={<span>Next.js · :3001</span>}
        />
      </BothThemes>
    </Section>
  );
}

const SETTINGS_SECTIONS: SettingsNavItem[] = [
  ...[
    "General",
    "Variables",
    "Secret managers",
    "Console address",
    "Integrations",
    "About Noust",
  ].map((label, index) => ({
    to: `/settings/${label.toLowerCase().replaceAll(" ", "-")}`,
    label,
    exact: index === 0,
    group: "This server",
    scope: <Mono tone="default">web-2</Mono>,
    scopeKey: "web-2",
  })),
  ...[
    ["Channels", "Notifications"],
    ["Alert rules", "Alerts"],
    ["Probes and status page", "Probes"],
    ["Logs and metrics export", "Log shipping"],
  ].map(([label, keywords]) => ({
    to: `/settings/${(label ?? "").toLowerCase().replaceAll(" ", "-")}`,
    label: label ?? "",
    keywords: keywords ?? "",
    group: "Monitoring",
    scope: <Mono tone="default">web-2</Mono>,
    scopeKey: "web-2",
  })),
  ...["API tokens", "API reference", "Manifest", "MCP"].map((label) => ({
    to: `/settings/${label.toLowerCase().replaceAll(" ", "-")}`,
    label,
    group: "Developers",
    scope: <Mono tone="default">web-2</Mono>,
    scopeKey: "web-2",
  })),
  ...["Accounts", "Roles", "Single sign-on", "Approvals"].map((label) => ({
    to: `/settings/${label.toLowerCase().replaceAll(" ", "-")}`,
    label,
    group: "People and access",
    scope: "Central",
    scopeKey: "central",
  })),
  ...["Audit log", "Controls", "ENS report"].map((label) => ({
    to: `/settings/${label.toLowerCase().replaceAll(" ", "-")}`,
    label,
    group: "Governance",
    scope: "Central",
    scopeKey: "central",
  })),
];

function LongSettings40() {
  return (
    <Section
      id="k40-long-settings"
      title="A long list of settings"
      description="Twenty-one sections under headings that say whose they are: on a fleet the server's name leads each heading of its block, and the instance's blocks say Central. From twelve sections a box narrows the list by name and by the old names a section keeps. A list taller than the screen scrolls inside it, below the top bar."
    >
      <Stage plain>
        <SettingsLayout
          label="Settings (specimen)"
          items={SETTINGS_SECTIONS}
          index
        >
          <Card title="Channels" description="Where the notices go.">
            <p className="text-13 text-fg-muted">
              Type in the box to narrow the list: try &quot;notif&quot;, the old
              name of Channels.
            </p>
          </Card>
        </SettingsLayout>
      </Stage>
      <DoDont
        rule="A long list says whose each section is"
        doThis={
          <ul className="flex flex-col gap-0.5 text-13">
            <li className="px-2.5 text-12 font-medium text-fg-muted">
              <Mono tone="default">web-2</Mono> · Monitoring
            </li>
            <li className="px-2.5 py-1 text-fg-muted">Channels</li>
            <li className="px-2.5 py-1 text-fg-muted">Alert rules</li>
          </ul>
        }
        notThis={
          <ul className="flex flex-col gap-0.5 text-13">
            <li className="px-2.5 py-1 text-fg-muted">Channels</li>
            <li className="px-2.5 py-1 text-fg-muted">Alert rules</li>
            <li className="px-2.5 py-1 text-fg-muted">Accounts</li>
            <li className="px-2.5 py-1 text-fg-muted">Roles</li>
          </ul>
        }
        why="Twenty-four sections in a flat list do not fit a screen, and nothing says which are this server's and which the central's, or on which server of a fleet they act."
      />
    </Section>
  );
}

function DashboardTabs40() {
  return (
    <Section
      id="k40-dashboard-tabs"
      title="A dashboard with tabs"
      description="The Overview and its Map are tabs of one page under one header. The tab that is the dashboard fills the figure, attention and chart slots; any other tab passes its own body."
    >
      <Stage plain>
        <DashboardPage
          header={{
            title: "Overview",
            primaryAction: <Button variant="primary">New application</Button>,
          }}
          tabs={
            <LinkTabs
              label="Overview views"
              tabs={APP_TABS.slice(0, 2).map((tab) => ({
                ...tab,
                params: { domain: "shop.example.com" },
              }))}
            />
          }
          figures={[
            <StatTile
              key="apps"
              label="Applications"
              value="14 of 17"
              detail="running"
            />,
            <StatTile
              key="failed"
              label="Failed"
              value="2"
              detail="worker, labs"
            />,
            <StatTile key="disk" label="Disk" value="61%" detail="of 480 GB" />,
          ]}
        />
      </Stage>
    </Section>
  );
}

interface Backup {
  name: string;
  at: string;
}

const BACKUP_COLUMNS: Column<Backup>[] = [
  { id: "name", header: "Backup", cell: (row) => row.name },
  { id: "at", header: "Taken", cell: (row) => row.at, mono: true },
];

function Tables40() {
  return (
    <Section
      id="k40-table"
      title="A table that waits"
      description="Rows that have not arrived are a wait, not an empty list: pass the query's data as it comes and the skeleton is drawn until there is an answer; only an empty answer draws the empty state."
    >
      <div className="grid gap-6 lg:grid-cols-2">
        <Item label="rows={undefined}: not loaded" className="w-full">
          <div className="w-full">
            <DataTable
              caption="Backups, not loaded"
              columns={BACKUP_COLUMNS}
              rows={undefined}
              getRowId={(row) => row.name}
              skeletonRows={2}
              empty={<EmptyState variant="inline" title="No backups yet." />}
            />
          </div>
        </Item>
        <Item label="rows={[]}: none" className="w-full">
          <div className="w-full">
            <DataTable
              caption="Backups, none"
              columns={BACKUP_COLUMNS}
              rows={[]}
              getRowId={(row) => row.name}
              empty={<EmptyState variant="inline" title="No backups yet." />}
            />
          </div>
        </Item>
      </div>
      <DoDont
        rule="No answer yet is not an empty answer"
        doThis={
          <DataTable
            caption="Backups, waiting"
            columns={BACKUP_COLUMNS}
            rows={undefined}
            getRowId={(row) => row.name}
            skeletonRows={2}
            empty={<EmptyState variant="inline" title="No backups yet." />}
          />
        }
        notThis={
          <DataTable
            caption="Backups, empty before the answer"
            columns={BACKUP_COLUMNS}
            rows={[]}
            getRowId={(row) => row.name}
            empty={<EmptyState variant="inline" title="No backups yet." />}
          />
        }
        why="A page that reads its rows with `?? []` says 'No backups yet' for a second and then shows eleven. Pass the data as it comes."
      />
    </Section>
  );
}

/** Specimens of the components of the 4.0 console wave. */
export function Kit40() {
  return (
    <>
      <Settings40 />
      <InlineMessages40 />
      <Leave40 />
      <Confirm40 />
      <Split40 />
      <Rows40 />
      <Fold40 />
      <Groups40 />
      <Skeletons40 />
      <Place40 />
      <LongSettings40 />
      <DashboardTabs40 />
      <Tables40 />
    </>
  );
}
