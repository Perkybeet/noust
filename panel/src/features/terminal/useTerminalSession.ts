/**
 * The page's view of a terminal session: the connection (`./connection`) as React state.
 *
 * The connection is plain TypeScript with the network passed in, which is what its tests drive;
 * this is the part that knows about the active server, the console's ticket and the browser's
 * WebSocket, and that ends the connection when the page goes away. The shell is not ended with it:
 * the server keeps it for the minute a reload takes.
 */

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { nodeSocketPath } from "../../api/nodeScope";
import { wsTicket } from "../../api/queries/auth";
import type { ScreenSource } from "../../components/ui/terminal/controller";
import { useNode } from "../../nodes/useNode";
import { socketUrl } from "../../realtime/sockets";
import { TerminalConnection } from "./connection";
import type { Dependencies, OpenOptions, SessionState, TerminalSocket, TerminalTarget } from "./connection";

export type { Ended, Failure, LimitNotice, OpenOptions, Phase, SessionInfo, SessionState, TerminalTarget } from "./connection";
export { RECONNECT_WINDOW_MS } from "./connection";

/** For tests: how a socket is made and where the console's own ticket comes from. */
export interface Injected {
  connect?: (url: string) => TerminalSocket;
  getTicket?: () => Promise<string>;
  now?: () => number;
}

export interface TerminalSession {
  state: SessionState;
  /** The emulator and when it changes: what `TerminalScreen` draws. */
  source: ScreenSource;
  open: (options?: OpenOptions) => void;
  /** Ends the shell. */
  end: () => void;
  /** Sends what was typed. */
  send: (data: string) => void;
  /** Tells the shell its screen changed size. */
  resize: (cols: number, rows: number) => void;
  /** Back to the start, with an empty screen: another terminal. */
  restart: () => void;
}

/** The things a connection reads each time it needs them, kept current by the hook. */
class Environment {
  private node: string | null = null;
  private injected: Injected = {};

  update(node: string | null, injected: Injected): void {
    this.node = node;
    this.injected = injected;
  }

  dependencies(): Dependencies {
    return {
      node: () => this.node,
      platformTicket: () => (this.injected.getTicket ?? wsTicket)(),
      address: socketUrl,
      nodePath: nodeSocketPath,
      connect: (url) => (this.injected.connect ?? ((address: string): TerminalSocket => new WebSocket(address)))(url),
      now: () => (this.injected.now ?? Date.now)(),
    };
  }
}

/**
 * Opens and holds one terminal session on a target.
 *
 * @param target An application, or the server itself.
 * @param injected For tests.
 */
export function useTerminalSession(target: TerminalTarget, injected: Injected = {}): TerminalSession {
  const { node } = useNode();
  // What a socket is built from is read when it is built, not when the hook renders: the server on
  // screen may have changed since the session opened.
  const [environment] = useState(() => new Environment());
  useEffect(() => {
    environment.update(node, injected);
  });

  const domain = target.kind === "app" ? target.domain : null;
  const connection = useMemo(
    () => new TerminalConnection(domain === null ? { kind: "server" } : { kind: "app", domain }, environment.dependencies()),
    [domain, environment],
  );

  useEffect(() => {
    return () => {
      connection.dispose();
    };
  }, [connection]);

  const state = useSyncExternalStore(connection.subscribe, connection.getState);
  const source = connection.getSource();

  return useMemo(
    () => ({
      state,
      source,
      open: (options?: OpenOptions) => {
        connection.open(options);
      },
      end: () => {
        connection.end();
      },
      send: (data: string) => {
        connection.send(data);
      },
      resize: (cols: number, rows: number) => {
        connection.resize(cols, rows);
      },
      restart: () => {
        connection.restart();
      },
    }),
    [connection, state, source],
  );
}
