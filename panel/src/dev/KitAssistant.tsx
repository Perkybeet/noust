import { useState } from "react";

import { AiMark } from "../components/assistant/AiMark";
import { ExplainButton } from "../components/assistant/ExplainButton";
import { LiteralLines, MessageText } from "../components/assistant/MessageText";
import { NotOffered, ProposalCard } from "../components/assistant/ProposalCard";
import type { ProposalView } from "../components/assistant/ProposalCard";
import { ReadSteps } from "../components/assistant/ReadSteps";
import { UsageLine } from "../components/assistant/UsageLine";
import { PolicyMatrix } from "../components/page/PolicyMatrix";
import type { PolicyCellModel, PolicyRowModel, PolicyValue } from "../components/page/PolicyMatrix";
import { PlanPreview } from "../components/ui/PlanPreview";
import type { Plan } from "../components/ui/PlanPreview";
import { textLinkClassName } from "../components/ui/TextLink";
import { BothThemes, DoDont, Item, Section } from "./gallery";

/** A plan as the server answers it for an update: ordered phases, a file that is only names, and what it cannot know. */
const UPDATE_PLAN: Plan = {
  operation: "app.update",
  subject: "shop.example.com",
  effect: { code: "plans.update.release", params: { domain: "shop.example.com", branch: "main" }, text: "Builds shop.example.com from main as a new release and switches to it once it answers the startup check. The release that serves now stays on disk to go back to.", hint: null },
  commands: [
    { argv: ["npm", "install"], text: "npm install", probe: false, cwd: null, runs_as: "sandbox:noust-build", step: "step-1", writes_to: null, check: false, seconds: null },
    { argv: ["systemctl", "restart", "shop-example-com.service"], text: "systemctl restart shop-example-com.service", probe: false, cwd: null, runs_as: null, step: "step-2", writes_to: null, check: false, seconds: null },
    { argv: ["GET", "http://127.0.0.1:3100/"], text: "GET http://127.0.0.1:3100/", probe: true, cwd: null, runs_as: null, step: "step-2", writes_to: null, check: true, seconds: 30 },
  ],
  files: [
    {
      path: "/var/www/apps/shop-example-com/releases/20261113-021515/.noust-env",
      change: "create",
      shown: "names",
      lines: [
        { kind: "add", text: "NOUST_BRANCH" },
        { kind: "add", text: "NOUST_RELEASE" },
      ],
      truncated: false,
      reason: null,
      destination: null,
    },
    { path: "/var/www/apps/shop-example-com/current", change: "link", shown: "none", lines: [], truncated: false, reason: null, destination: "releases/20261113-021515" },
  ],
  steps: [
    { key: "step-1", title: "Installing dependencies", commands: [0] },
    { key: "step-2", title: "Activating release 20261113-021515", commands: [1, 2] },
  ],
  findings: [{ code: "plans.found.serving_now", params: { release: "20261113-021500" }, text: "Release 20261113-021500 serves now", hint: null }],
  unknown: [
    { code: "plans.unknown.build", params: {}, text: "Which install and build commands run, whether they pass and how long they take: they come from the repository's own files and scripts, which making a plan does not fetch or run", hint: null },
    { code: "plans.unknown.health", params: { check: "GET http://127.0.0.1:3100/" }, text: "Whether the new version answers the startup check: it does not exist yet. If it does not, the version that serves now comes back", hint: null },
  ],
  needs: { elevation: false, approval: false, confirmations: [{ kind: "frozen", field: "frozen_ok", sentence: { code: "plans.confirm.frozen", params: { domain: "shop.example.com", until: "2026-11-13T04:15:15+00:00", reason: "Black Friday" }, text: "Deploys of shop.example.com are frozen until 2026-11-13T04:15:15+00:00: Black Friday", hint: null } }] },
  blocked: null,
  asked_remote: true,
  omitted: 0,
};

const MODEL_ANSWER = [
  "The deployment failed because the build could not find **PORT** in the environment.",
  "",
  "1. Open the variables of the application and add `PORT`.",
  "2. Deploy again.",
  "",
  "The [documentation of the framework](https://docs.example.org/deploy) says the same.",
].join("\n");

/** What a hostile text does to a reader that is not careful: a tag, a script, an image that phones home and a link that lies. */
const HOSTILE_ANSWER = [
  'Restart it. <img src=x onerror="alert(1)"> <script>steal()</script>',
  "",
  "![tracking pixel](https://tracker.example.net/p.gif)",
  "",
  "[https://noust.example/docs](https://evil.example.net/login) and [run me](javascript:alert(1)).",
].join("\n");

const PROPOSAL: ProposalView = {
  id: "p1",
  title: "Restart shop.example.com",
  klass: "ask",
  state: "waiting",
  permission: "apps.operate",
  call: "POST /api/apps/shop.example.com/restart",
  facts: [
    { label: "Application", value: "shop.example.com" },
    { label: "Server", value: "web-01" },
  ],
  plan: UPDATE_PLAN,
  why: "Your policy asks you before the assistant restarts something in production.",
};

function row(permission: string, label: string, key: boolean, cells: Partial<Record<string, Partial<PolicyCellModel>>>): PolicyRowModel {
  const base = (caller: "token" | "assistant", environment: "production" | "other"): PolicyCellModel => {
    const own = cells[`${caller}.${environment}`] ?? {};
    return {
      caller,
      environment,
      value: caller === "token" ? "alone" : "propose",
      default: caller === "token" ? "alone" : "propose",
      offered: key ? ["propose", "never"] : ["alone", "propose", "never"],
      fixed: false,
      reason: null,
      legacy: false,
      ...own,
    };
  };
  return { permission, label, key, cells: [base("token", "production"), base("token", "other"), base("assistant", "production"), base("assistant", "other")] };
}

const POLICY_ROWS: readonly PolicyRowModel[] = [
  row("apps.operate", "Start, stop and restart; renew certificates", false, { "assistant.other": { value: "alone" } }),
  row("apps.manage", "Create, configure and delete applications and domains", true, { "token.production": { value: "alone", offered: ["propose", "never"], reason: "confirm", legacy: true } }),
  row("server.terminal", "The server's terminal", true, { "assistant.production": { value: "never", default: "never", fixed: true, reason: "assistant_floor", offered: ["never"] }, "assistant.other": { value: "never", default: "never", fixed: true, reason: "assistant_floor", offered: ["never"] } }),
];

function Policy() {
  const [rows, setRows] = useState(POLICY_ROWS);
  const change = (target: PolicyRowModel, cell: PolicyCellModel, value: PolicyValue): void =>
    setRows((current) =>
      current.map((candidate) =>
        candidate.permission === target.permission
          ? { ...candidate, cells: candidate.cells.map((c) => (c.caller === cell.caller && c.environment === cell.environment ? { ...c, value } : c)) }
          : candidate,
      ),
    );
  return <PolicyMatrix rows={rows} caption="Approval policy, Applications" onChange={change} />;
}

/**
 * The pieces of the built-in assistant and of the policy of who calls (items 171, 183 and 184), drawn from the same components the
 * console uses. The assistant is an AI and says so; its text is drawn without HTML; what it proposes is a card the console draws
 * from the exact call; what Noust's simulation says an operation would do is "See what it will do", for people and for the
 * assistant alike.
 */
export function KitAssistant() {
  return (
    <>
      <Section
        id="ka-mark"
        title="The mark of an AI"
        description="Every answer carries it, fixed and small, with the model that wrote it and where that model runs, so a person can weigh what they read. It is achromatic because it is a fact and not a state, and for a screen reader it is one sentence."
      >
        <BothThemes>
          <div className="flex flex-col gap-4">
            <Item label="A service in the European Union">
              <AiMark model="mistral-small-latest" where="In the European Union" at="10:42" />
            </Item>
            <Item label="A model on the person's own network">
              <AiMark model="qwen3-14b" where="On your network" />
            </Item>
          </div>
        </BothThemes>
      </Section>

      <Section
        id="ka-text"
        title="The text of an answer"
        description="A reply is text from outside, so it is read into paragraphs, lists, code, bold and links and drawn as elements. Nothing goes through innerHTML, there is no image, a tag stays as the characters it is, and a link shows where it goes and opens only when pressed."
      >
        <BothThemes>
          <div className="flex flex-col gap-6">
            <Item label="An ordinary answer">
              <MessageText text={MODEL_ANSWER} />
            </Item>
            <Item label="The system's own words, from Noust's copy, with their lines">
              <LiteralLines caption="The log of deployment 14, lines 41 to 42" lines={[{ n: 41, text: "Error: listen EADDRINUSE: address already in use :::3100" }, { n: 42, text: "    at Server.setupListenHandle [as _listen2]" }]} />
            </Item>
            <Item label="A hostile answer: the tag, the script and the image are text; the lying link shows its real host">
              <MessageText text={HOSTILE_ANSWER} />
            </Item>
          </div>
        </BothThemes>
        <DoDont
          rule="Never turn a model's words into markup"
          doThis={<MessageText text={"Add `PORT` and [read this](https://docs.example.org/)."} />}
          notThis={<code>{"dangerouslySetInnerHTML={{ __html: answer }}"}</code>}
          why="The content security policy and Trusted Types forbid a string-to-DOM sink, and a model's words can be anything somebody wrote in a log it read."
        />
      </Section>

      <Section
        id="ka-steps"
        title="What it read"
        description="One line for each thing it read, in Noust's words and not the model's. While it works the list is open; when it ends it folds under the answer. Reading is neutral, a read that failed has the failed glyph and a word, and what was hidden before it left is said under the line that read it."
      >
        <BothThemes>
          <div className="flex flex-col gap-6">
            <Item label="Working">
              <ReadSteps running steps={[{ id: "1", what: "Read the log of deployment 14", size: "120 lines", state: "done", masked: 2 }, { id: "2", what: "Read the names of the variables of shop.example.com", state: "running" }]} />
            </Item>
            <Item label="Finished, folded under the answer">
              <ReadSteps defaultOpen steps={[{ id: "1", what: "Read the log of deployment 14", size: "120 lines", state: "done", outside: true }, { id: "2", what: "Read the journal of shop.example.com", state: "failed", error: "The server does not answer." }]} />
            </Item>
          </div>
        </BothThemes>
      </Section>

      <Section
        id="ka-proposal"
        title="A proposal"
        description="Drawn by the console from the exact call. It says what it will do on what, which of four classes it is (done on its own, asks for approval, also asks you to confirm it is you, asks a second person), shows Noust's simulation where the operation has one and ends in the two buttons that decide it. One primary, never an approve-all. A value that is neither the person's nor Noust's is highlighted with a shape, a ground and a word."
      >
        <BothThemes stacked>
          <div className="grid gap-4 lg:grid-cols-2">
            <Item label="Asks for approval, with its simulation folded">
              <div className="w-full">
                <ProposalCard proposal={PROPOSAL} primary links={{ policy: <a href="#ka-policy" className={textLinkClassName("ui")}>See the approval policy</a> }} />
              </div>
            </Item>
            <Item label="A value that is neither yours nor Noust's">
              <div className="w-full">
                <ProposalCard
                  proposal={{
                    ...PROPOSAL,
                    id: "p2",
                    title: "Add the domain cdn.example.org to shop.example.com",
                    plan: null,
                    facts: [{ label: "Application", value: "shop.example.com" }, { label: "Domain", value: "cdn.example.org", foreign: { kind: "host", where: "Found in the log of deployment 14, line 41." } }],
                  }}
                />
              </div>
            </Item>
            <Item label="Done on its own, as a record">
              <div className="w-full">
                <ProposalCard proposal={{ ...PROPOSAL, id: "p3", klass: "alone", state: "done", plan: null, decidedAt: "10:44", result: "Restarted shop.example.com. It answers the startup check." }} />
              </div>
            </Item>
            <Item label="Failed, with the system's words">
              <div className="w-full">
                <ProposalCard proposal={{ ...PROPOSAL, id: "p4", state: "failed", plan: null, result: "Could not restart shop.example.com.", output: "Job for shop-example-com.service failed because the control process exited with error code." }} />
              </div>
            </Item>
            <Item label="Not offered: a line, never a disabled button">
              <div className="w-full">
                <NotOffered title="Delete the application shop.example.com" why="Your policy does not let the assistant do this." hint="Whoever administers security changes it." policyLink={<a href="#ka-policy" className={textLinkClassName("inline")}>See the approval policy</a>} />
              </div>
            </Item>
          </div>
        </BothThemes>
      </Section>

      <Section
        id="ka-plan"
        title="See what it will do"
        description="The orders in the order they would run, the files they would change and, never folded away, what the simulation does not know. It is Noust's own simulation, so a person's dialog and a proposal draw the same thing. An operation without a simulation does not draw it at all."
      >
        <BothThemes>
          <PlanPreview plan={UPDATE_PLAN} defaultOpen />
        </BothThemes>
      </Section>

      <Section
        id="ka-usage"
        title="Usage of the limits"
        description="How much of its limits the conversation has used, in one slim meter for the limit that is nearest, with a sentence only when it is near. The others are behind the question mark."
      >
        <BothThemes>
          <div className="flex w-72 max-w-full flex-col gap-6">
            <Item label="Plenty left">
              <UsageLine readings={[{ id: "tokens", label: "What it has read", used: 120_000, cap: 400_000, valueText: "120 k of 400 k" }]} />
            </Item>
            <Item label="Near the limit">
              <UsageLine readings={[{ id: "tokens", label: "What it has read", used: 340_000, cap: 400_000, valueText: "340 k of 400 k" }]} />
            </Item>
          </div>
        </BothThemes>
      </Section>

      <Section id="ka-explain" title="Explain this" description="It sits in the head of the block that holds the system's own words, never in the page's header. Where the assistant is off, or the person may not use it, there is nothing: not a disabled button.">
        <BothThemes>
          <ExplainButton onExplain={() => undefined} />
        </BothThemes>
      </Section>

      <Section
        id="ka-policy"
        title="The policy of who calls"
        description="A token and the assistant, each in production and in the other environments, with three values per cell. The padlock is the floor and opens nothing; the key marks what asks you to confirm it is you, where “Does it” is not offered; “Factory” is said only where the value differs."
      >
        <BothThemes stacked>
          <Policy />
        </BothThemes>
      </Section>
    </>
  );
}
