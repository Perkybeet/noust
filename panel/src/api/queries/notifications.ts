import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

/** The channels, without a credential, and the types a new one may be. */
export type NotificationChannelList = ResponseOf<"/api/notifications/channels", "get">;
export type NotificationChannel = NotificationChannelList["items"][number];
export type ChannelTypeInfo = NotificationChannelList["types"][number];
export type ChannelBody = BodyOf<"/api/notifications/channels/{name}", "put">;

/** One notification on its way to one channel, and what became of it. */
export type NotificationDeliveries = ResponseOf<"/api/notifications/deliveries", "get">;
export type NotificationDelivery = NotificationDeliveries["items"][number];

export const notificationKeys = {
  channels: ["notifications", "channels"] as const,
  deliveries: ["notifications", "deliveries"] as const,
};

export const notificationChannelsQuery = () =>
  queryOptions({
    queryKey: notificationKeys.channels,
    queryFn: ({ signal }) => request("get", "/api/notifications/channels", { signal }),
  });

/**
 * The record of deliveries, newest first. Read again every half minute while the page is open:
 * a delivery that waits for its next try is exactly what someone is looking at it for.
 */
export const notificationDeliveriesQuery = () =>
  queryOptions({
    queryKey: notificationKeys.deliveries,
    queryFn: ({ signal }) => request("get", "/api/notifications/deliveries", { query: { limit: 30 }, signal }),
    refetchInterval: 30_000,
  });

/**
 * Creates a channel or changes one; what the body leaves out stays. A credential sent back as
 * "***" keeps the stored one. Needs a recent "Confirm it's you"; the client asks for it.
 */
export function saveNotificationChannel(name: string, body: ChannelBody) {
  return request("put", "/api/notifications/channels/{name}", { params: { name }, body });
}

/** Removes a channel of one's own, or empties a default one. */
export function removeNotificationChannel(name: string) {
  return request("delete", "/api/notifications/channels/{name}", { params: { name } });
}

/** Sends a failed, waiting or held delivery again now. */
export function retryNotificationDelivery(id: number) {
  return request("post", "/api/notifications/deliveries/{delivery_id}/retry", { params: { delivery_id: id } });
}
