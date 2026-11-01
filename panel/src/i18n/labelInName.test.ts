import { beforeAll, describe, expect, it } from "vitest";

import { loadCatalog, translate } from ".";
import type { Locale } from ".";
import { nameHoldsLabel } from "../test/labelInName";

/**
 * A control that draws words and is named by an `aria-label` has to be named by those words first
 * (WCAG 2.5.3, Label in Name): whoever says "Open service" to a voice control reaches the link whose
 * name is "Open service queue-worker", and not the one that reads "Open the service queue-worker".
 * `e2e/pages.spec.ts` holds the rendered pages to it with axe; these are the pairs of texts in the
 * catalogs that are drawn and named apart, in both languages, so a rewrite of either one fails here
 * before it fails in a browser, and so does a Spanish text the English pass never reads.
 */
type Pair = [what: string, texts: (locale: Locale) => [drawn: string, name: string]];

const PAIRS: Pair[] = [
  ["the attention action to read a deploy's log", (l) => [translate(l, "overview.attention.viewLog"), translate(l, "overview.attention.viewLogAria", { domain: "shop.example.com" })]],
  ["the attention action to diagnose an app", (l) => [translate(l, "overview.attention.diagnose"), translate(l, "overview.attention.diagnoseAria", { domain: "shop.example.com" })]],
  ["the attention action to look at a certificate", (l) => [translate(l, "overview.attention.certificate"), translate(l, "overview.attention.certificateAria", { domain: "shop.example.com" })]],
  ["the attention action to open a service", (l) => [translate(l, "overview.attention.openService"), translate(l, "overview.attention.openServiceAria", { name: "queue-worker" })]],
  ["the attention action to look at a process", (l) => [translate(l, "overview.attention.openFinding"), translate(l, "overview.attention.openFindingAria", { name: "node" })]],
  ["the attention action to open the probes", (l) => [translate(l, "inbox.actions.openProbes"), translate(l, "inbox.actions.openProbesAria", { name: "blog.example.org" })]],
  ["the attention action to review a request", (l) => [translate(l, "inbox.actions.review"), translate(l, "inbox.actions.reviewAria", { id: "a1" })]],
  ["the attention action to open the updates", (l) => [translate(l, "inbox.actions.openUpdates"), translate(l, "inbox.actions.openUpdatesAria")]],
  ["the attention action to review a recipe's update", (l) => [translate(l, "inbox.actions.reviewRecipe"), translate(l, "inbox.actions.reviewRecipeAria", { domain: "shop.example.com" })]],
  ["the action to take an alert", (l) => [translate(l, "inbox.actions.acknowledge"), translate(l, "inbox.actions.acknowledgeAria", { subject: "disk" })]],
  ["the action to hand an alert back", (l) => [translate(l, "inbox.actions.reopen"), translate(l, "inbox.actions.reopenAria", { subject: "disk" })]],
  ["the link that opens a preview from its row", (l) => [translate(l, "appSettings.previews.openPreview"), translate(l, "previewHand.list.openAria", { name: "pr-42" })]],
  ["the link that opens a preview from the overview", (l) => [translate(l, "appPages.overview.previewOpen"), translate(l, "appPages.overview.previewOpenAria", { name: "pr-42" })]],
  ["a recipe's documentation link", (l) => [translate(l, "catalog.card.docs"), translate(l, "catalog.card.docsOf", { title: "Ghost" })]],
  ["the link to an installation's settings on GitHub", (l) => [translate(l, "settings.integrations.github.installations.manage"), translate(l, "settings.integrations.github.installations.manageLabel", { account: "acme" })]],
  ["the live site link of an arrival", (l) => ["https://shop.example.com", translate(l, "apps.arrival.liveSite", { domain: "https://shop.example.com" })]],
  ["the live site link of an app's header", (l) => ["shop.example.com", translate(l, "appPages.layout.liveSite", { domain: "shop.example.com" })]],
  ["the deploy in the list of an app's metrics", (l) => ["#25 14:01", translate(l, "appPages.metrics.deployMarkLabel", { id: "25", when: "14:01", status: "succeeded" })]],
  ["the button to sign a certificate again", (l) => [translate(l, "internalCa.renew"), translate(l, "internalCa.renewLabel", { name: "intranet" })]],
  ["the button to send a notification again", (l) => [translate(l, "notifyRouting.deliveries.retry"), translate(l, "notifyRouting.deliveries.retryLabel", { subject: "Backup failed", channel: "Slack" })]],
  ["the button to put a setting back to the fleet's value", (l) => [translate(l, "fleetSettings.actions.putBack"), translate(l, "fleetSettings.actions.putBackLabel", { name: "retention", server: "web-2" })]],
  ["the button of a job that was asked to stop", (l) => [translate(l, "queue.cancel.stopping"), translate(l, "queue.cancel.stoppingLabel", { name: "Updating shop.example.com" })]],
  ["the button that cancels a job", (l) => [translate(l, "common.words.cancel"), translate(l, "queue.cancel.label", { name: "Updating shop.example.com" })]],
  ["the button that opens a cleanup's details", (l) => [translate(l, "server.storage.history.details"), translate(l, "server.storage.history.detailFor", { id: 7 })]],
];

describe("what a control draws, and the name it is given", () => {
  beforeAll(async () => {
    await loadCatalog("es");
  });

  it.each(PAIRS)("holds, in English and in Spanish, for %s", (_what, texts) => {
    for (const locale of ["en", "es"] as const) {
      const [drawn, name] = texts(locale);
      expect(nameHoldsLabel(drawn, name), `${locale}: the name "${name}" does not hold "${drawn}"`).toBe(true);
    }
  });
});
