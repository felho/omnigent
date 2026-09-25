// Subscribes to Arca auto-connect status from the desktop shell and surfaces
// it as sonner toasts. Mount once per window (in AppShell). Also exports a
// lightweight useArcaStatus() for consumers that only need to read status.

import { createElement, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import {
  type ArcaStatus,
  getArcaStatus,
  onArcaStatusChanged,
  retryArcaConnect,
} from "@/lib/nativeBridge";
import { copyText } from "@/lib/clipboard";
import { fetchHosts } from "@/hooks/useHosts";
import { writeArcaHostId } from "@/lib/arcaHost";

export type { ArcaStatus };

// Stable toast id so subsequent updates replace the same toast.
const TOAST_ID = "arca-autoconnect";

// Sonner merges options into a toast that reuses an id, so each state resets
// the fields an earlier state may have set.
const RESET = { description: undefined, action: undefined, cancel: undefined };

// How long the starting state must persist before showing a progress toast.
const STARTING_DELAY_MS = 1500;

// Module-level: track which failure timestamps have already been toasted so
// a remount (e.g. the component unmounting and remounting) doesn't re-show
// the same error. Keyed by finishedAt.
const toastedFailures = new Set<number>();

/** Exposed for tests only — do not call in production code. */
export function clearToastedFailuresForTest(): void {
  toastedFailures.clear();
}

// How long to wait for the new host to appear in the host list after online.
const POLL_DEADLINE_MS = 30_000;
const POLL_INTERVAL_MS = 1_500;

/** Show the exact command and its output tail, preserving line breaks. */
function showArcaDetails(status: ArcaStatus): void {
  const text = status.output
    ? `$ ${status.command ?? ""}\n\n${status.output}`
    : `$ ${status.command ?? ""}`;
  toast("Arca auto-connect", {
    description: createElement(
      "pre",
      { className: "max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono text-xs" },
      text,
    ),
    duration: 15_000,
  });
}

/** Copy a command the user must run themselves, confirming with a toast. */
function copyCommand(command: string): void {
  void copyText(command).then(() => {
    toast.success(`Copied \`${command}\``, { duration: 2_000 });
  });
}

/**
 * Subscribe to live Arca auto-connect status and expose it as React state.
 * The initial value is fetched once on mount; subsequent changes arrive via
 * the shell's push subscription.
 *
 * Returns null while loading (first fetch pending) or outside Electron.
 */
export function useArcaStatus(): ArcaStatus | null {
  const [status, setStatus] = useState<ArcaStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getArcaStatus().then((s) => {
      if (!cancelled) setStatus(s);
    });
    const unsubscribe = onArcaStatusChanged((s) => {
      if (!cancelled) setStatus(s);
    });
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  return status;
}

/**
 * Mount once in AppShell. Watches the Arca auto-connect status and shows
 * sonner toasts for starting / online / failed transitions. On success it
 * also detects the newly-online host and records it via writeArcaHostId.
 */
export function useArcaAutoConnect(): void {
  const queryClient = useQueryClient();
  // Timer ref for the 1500 ms delay before showing the "starting" toast.
  const startingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Snapshot of online host ids taken when starting is first observed, so we
  // can identify the newly-online host once the run succeeds.
  const onlineSnapshotRef = useRef<Set<string> | null>(null);
  // Whether we showed a loading toast (and thus need to dismiss or replace it).
  const shownLoadingRef = useRef(false);
  // Tracks the most recent state seen, so async branches can bail if a later
  // status pushed before an async step completed made the branch stale.
  const currentStateRef = useRef<ArcaStatus["state"] | null>(null);

  useEffect(() => {
    let cancelled = false;

    function clearStartingTimer() {
      if (startingTimerRef.current !== null) {
        clearTimeout(startingTimerRef.current);
        startingTimerRef.current = null;
      }
    }

    async function handleStatus(status: ArcaStatus) {
      if (cancelled) return;
      currentStateRef.current = status.state;

      if (status.state === "starting") {
        // Snapshot online hosts the first time we see "starting".
        if (onlineSnapshotRef.current === null) {
          const hosts = await queryClient
            .fetchQuery({
              queryKey: ["hosts", { includeSandbox: false }],
              queryFn: () => fetchHosts(false),
              staleTime: 0,
            })
            .catch(() => null);
          if (cancelled) return;
          // If a later status arrived while we were fetching, don't proceed
          // with the "starting" branch — the state has moved on.
          if (currentStateRef.current !== "starting") return;
          onlineSnapshotRef.current = new Set(
            (hosts ?? []).filter((h) => h.status === "online").map((h) => h.host_id),
          );
        }
        // Show the loading toast only after the delay, so a fast warm launch
        // shows nothing.
        if (startingTimerRef.current === null && !shownLoadingRef.current) {
          startingTimerRef.current = setTimeout(() => {
            startingTimerRef.current = null;
            if (cancelled) return;
            shownLoadingRef.current = true;
            toast.loading("Connecting Arca…", {
              ...RESET,
              id: TOAST_ID,
              description: "Running `isaac omni host` on your Arca instance",
              duration: Infinity,
              action: {
                label: "Details",
                onClick: () => {
                  void getArcaStatus().then((s) => {
                    if (s) showArcaDetails(s);
                  });
                },
              },
            });
          }, STARTING_DELAY_MS);
        }
        return;
      }

      // Leaving "starting" — cancel the pending show timer.
      clearStartingTimer();

      if (status.state === "online") {
        const snapshot = onlineSnapshotRef.current;
        onlineSnapshotRef.current = null;

        if (status.alreadyRunning) {
          // Warm launch: dismiss any loading toast silently and refresh hosts.
          if (shownLoadingRef.current) {
            toast.dismiss(TOAST_ID);
            shownLoadingRef.current = false;
          }
          await queryClient.invalidateQueries({ queryKey: ["hosts"] });
          return;
        }

        // Cold launch (new connection): replace loading toast with success.
        if (shownLoadingRef.current) {
          toast.success("Arca connected", { ...RESET, id: TOAST_ID, duration: 4_000 });
          shownLoadingRef.current = false;
        }
        await queryClient.invalidateQueries({ queryKey: ["hosts"] });

        // Discover the newly-online host and record it.
        if (snapshot !== null) {
          const deadline = Date.now() + POLL_DEADLINE_MS;
          /* oxlint-disable no-await-in-loop */
          while (Date.now() < deadline) {
            if (cancelled) return;
            const hosts = await queryClient
              .fetchQuery({
                queryKey: ["hosts", { includeSandbox: false }],
                queryFn: () => fetchHosts(false),
                staleTime: 0,
              })
              .catch(() => []);
            const fresh = hosts.find((h) => h.status === "online" && !snapshot.has(h.host_id));
            if (fresh) {
              writeArcaHostId(fresh.host_id);
              return;
            }
            await new Promise<void>((resolve) => {
              setTimeout(resolve, POLL_INTERVAL_MS);
            });
          }
          /* oxlint-enable no-await-in-loop */
        }
        return;
      }

      if (status.state === "failed") {
        onlineSnapshotRef.current = null;

        // Only show the toast once per failure (keyed by finishedAt). The
        // module-level set survives remounts so reopening the UI doesn't
        // re-flash the same error.
        if (status.finishedAt !== undefined && toastedFailures.has(status.finishedAt)) {
          return;
        }
        if (status.finishedAt !== undefined) toastedFailures.add(status.finishedAt);

        if (shownLoadingRef.current) {
          shownLoadingRef.current = false;
        }

        const kind = status.errorKind ?? "unknown";
        let action: { label: string; onClick: () => void } | undefined;

        if (kind === "timeout" || kind === "unreachable" || kind === "unknown") {
          action = {
            label: "Retry",
            onClick: () => void retryArcaConnect(),
          };
        } else if (kind === "omni-auth") {
          action = {
            label: "Copy login command",
            onClick: () => copyCommand(`isaac omni login ${window.location.origin}`),
          };
        } else if (kind === "arca-auth") {
          action = {
            label: "Copy arca login",
            onClick: () => copyCommand("arca login"),
          };
        } else if (kind === "missing-remote-cli") {
          action = {
            label: "Details",
            onClick: () => showArcaDetails(status),
          };
        }

        toast.error("Couldn't connect Arca", {
          ...RESET,
          id: TOAST_ID,
          description: status.error,
          duration: Infinity,
          action,
        });
        return;
      }

      // unavailable / disabled / idle: dismiss any lingering toast.
      if (shownLoadingRef.current) {
        toast.dismiss(TOAST_ID);
        shownLoadingRef.current = false;
      }
    }

    void getArcaStatus().then((s) => {
      if (!cancelled && s) void handleStatus(s);
    });
    const unsubscribe = onArcaStatusChanged((s) => {
      void handleStatus(s);
    });

    return () => {
      cancelled = true;
      clearStartingTimer();
      unsubscribe();
    };
  }, [queryClient]);
}
