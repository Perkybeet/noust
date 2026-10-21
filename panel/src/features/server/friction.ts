/**
 * The friction of a change to the machine itself (sshd, the firewall, its name): typing the host
 * name, which makes the operator read which server they are about to change (docs/DESIGN.md 6.2).
 * Without a name to type, because the identity did not load, it is one question.
 */

export type HostFriction = { friction: "type"; confirmText: string } | { friction: "simple"; confirmText?: undefined };

/** `type` when the host's name is known, `simple` when it is not or the action does not ask for it. */
export function typedHost(hostname: string | undefined, typed = true): HostFriction {
  return typed && hostname !== undefined && hostname !== "" ? { friction: "type", confirmText: hostname } : { friction: "simple" };
}
