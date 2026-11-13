import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";

/**
 * What the topics of a Telegram group are made from, and asking Noust to create them. Apart from
 * `notifications.ts` on purpose: only the routing drawer of a Telegram channel needs it, so it is
 * loaded with that drawer and not with the first page.
 */

export const topicKeys = {
  events: ["notifications", "events"] as const,
};

/** The events and their subjects: what the topics of a group are made from. */
export const notificationEventsQuery = () =>
  queryOptions({
    queryKey: topicKeys.events,
    queryFn: ({ signal }) => request("get", "/api/notifications/events", { signal }),
    staleTime: 5 * 60_000,
  });

/**
 * Asks the group of a Telegram channel, through its bot, to create the topics its subjects are
 * missing, and saves their numbers. A group that cannot take them is an answer (`problem`), not an
 * error. Needs a recent "Confirm it's you"; the client asks for it.
 */
export function createTelegramTopics(name: string, recreate = false) {
  return request("post", "/api/notifications/channels/{name}/topics", { params: { name }, body: { recreate } });
}
