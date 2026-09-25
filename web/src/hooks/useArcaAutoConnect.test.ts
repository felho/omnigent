import { act, cleanup, renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { getArcaStatus, onArcaStatusChanged } from "@/lib/nativeBridge";
import type { ArcaStatus } from "@/lib/nativeBridge";
import { fetchHosts } from "@/hooks/useHosts";
import { writeArcaHostId } from "@/lib/arcaHost";
import {
  useArcaAutoConnect,
  useArcaStatus,
  clearToastedFailuresForTest,
} from "./useArcaAutoConnect";

vi.mock("@/lib/nativeBridge", () => ({
  getArcaStatus: vi.fn(async () => null),
  onArcaStatusChanged: vi.fn(() => () => {}),
  retryArcaConnect: vi.fn(async () => null),
  setArcaAutoConnect: vi.fn(async () => null),
}));
vi.mock("@/hooks/useHosts", () => ({
  fetchHosts: vi.fn(async () => []),
}));
vi.mock("@/lib/arcaHost", () => ({
  writeArcaHostId: vi.fn(),
  readArcaHostId: vi.fn(() => null),
}));
vi.mock("sonner", () => ({
  toast: Object.assign(
    vi.fn(() => "t"),
    {
      loading: vi.fn(() => "loading-id"),
      success: vi.fn(() => "success-id"),
      error: vi.fn(() => "error-id"),
      dismiss: vi.fn(),
    },
  ),
}));

function wrapperWith(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);
}

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

beforeEach(() => {
  vi.clearAllMocks();
  clearToastedFailuresForTest();
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

// Helper: resolve pending microtasks + flush React state updates.
async function flushAsync() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("useArcaStatus", () => {
  it("returns null initially and then the fetched status", async () => {
    const status: ArcaStatus = { state: "idle", autoConnect: true, command: null };
    vi.mocked(getArcaStatus).mockResolvedValue(status);
    vi.mocked(onArcaStatusChanged).mockImplementation(() => () => {});

    const { result } = renderHook(() => useArcaStatus());
    expect(result.current).toBeNull();

    await flushAsync();
    expect(result.current).toEqual(status);
  });

  it("updates when onArcaStatusChanged fires", async () => {
    vi.mocked(getArcaStatus).mockResolvedValue(null);
    let pushStatus: (s: ArcaStatus) => void = () => {};
    vi.mocked(onArcaStatusChanged).mockImplementation((cb) => {
      pushStatus = cb;
      return () => {};
    });

    const { result } = renderHook(() => useArcaStatus());
    await flushAsync();

    const online: ArcaStatus = { state: "online", autoConnect: true, command: null };
    act(() => {
      pushStatus(online);
    });
    expect(result.current).toEqual(online);
  });

  it("unsubscribes on unmount", async () => {
    const unsub = vi.fn();
    vi.mocked(onArcaStatusChanged).mockReturnValue(unsub);
    vi.mocked(getArcaStatus).mockResolvedValue(null);

    const { unmount } = renderHook(() => useArcaStatus());
    await flushAsync();
    unmount();
    expect(unsub).toHaveBeenCalledOnce();
  });
});

describe("useArcaAutoConnect", () => {
  it("shows a loading toast after the 1500ms delay when starting", async () => {
    vi.useFakeTimers();
    let pushStatus: (s: ArcaStatus) => void = () => {};
    vi.mocked(getArcaStatus).mockResolvedValue(null);
    vi.mocked(onArcaStatusChanged).mockImplementation((cb) => {
      pushStatus = cb;
      return () => {};
    });
    vi.mocked(fetchHosts).mockResolvedValue([]);

    const qc = makeQueryClient();
    renderHook(() => useArcaAutoConnect(), { wrapper: wrapperWith(qc) });

    // Flush initial getArcaStatus call.
    await act(() => vi.runAllTimersAsync());

    const starting: ArcaStatus = {
      state: "starting",
      autoConnect: true,
      command: "arca ssh isaac omni host --server https://x --background",
    };
    // Push starting (triggers async snapshot fetch + starts the delay timer).
    act(() => pushStatus(starting));
    // Before the 1500ms delay fires: no loading toast yet.
    expect(toast.loading).not.toHaveBeenCalled();

    // Advance past the 1500ms delay.
    await act(() => vi.runAllTimersAsync());
    expect(toast.loading).toHaveBeenCalledWith(
      "Connecting Arca…",
      expect.objectContaining({ id: "arca-autoconnect", duration: Infinity }),
    );
  });

  it("does not show the loading toast when online arrives before 1500ms (warm launch)", async () => {
    vi.useFakeTimers();
    let pushStatus: (s: ArcaStatus) => void = () => {};
    vi.mocked(getArcaStatus).mockResolvedValue(null);
    vi.mocked(onArcaStatusChanged).mockImplementation((cb) => {
      pushStatus = cb;
      return () => {};
    });
    vi.mocked(fetchHosts).mockResolvedValue([]);

    const qc = makeQueryClient();
    renderHook(() => useArcaAutoConnect(), { wrapper: wrapperWith(qc) });
    await act(() => vi.runAllTimersAsync());

    const starting: ArcaStatus = { state: "starting", autoConnect: true, command: null };
    act(() => pushStatus(starting));
    // Online arrives immediately (warm launch: alreadyRunning) — before the delay.
    const online: ArcaStatus = {
      state: "online",
      autoConnect: true,
      command: null,
      alreadyRunning: true,
    };
    act(() => pushStatus(online));
    // Advance time — the pending delay timer should be cleared.
    await act(() => vi.runAllTimersAsync());
    expect(toast.loading).not.toHaveBeenCalled();
  });

  it("shows a success toast on cold online and invalidates hosts", async () => {
    vi.useFakeTimers();
    let pushStatus: (s: ArcaStatus) => void = () => {};
    vi.mocked(getArcaStatus).mockResolvedValue(null);
    vi.mocked(onArcaStatusChanged).mockImplementation((cb) => {
      pushStatus = cb;
      return () => {};
    });
    // snapshot returns [] so we can find a fresh host in the next poll.
    vi.mocked(fetchHosts)
      .mockResolvedValueOnce([]) // snapshot at starting
      .mockResolvedValue([{ host_id: "arca-1", name: "box", owner: "me", status: "online" }]);

    const qc = makeQueryClient();
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    renderHook(() => useArcaAutoConnect(), { wrapper: wrapperWith(qc) });
    await act(() => vi.runAllTimersAsync());

    const starting: ArcaStatus = { state: "starting", autoConnect: true, command: null };
    act(() => pushStatus(starting));
    await act(() => vi.runAllTimersAsync());
    expect(toast.loading).toHaveBeenCalledOnce();

    const online: ArcaStatus = { state: "online", autoConnect: true, command: null };
    act(() => pushStatus(online));
    // Flush the async chain (invalidateQueries + poll).
    await act(() => vi.runAllTimersAsync());

    expect(toast.success).toHaveBeenCalledWith(
      "Arca connected",
      expect.objectContaining({ id: "arca-autoconnect" }),
    );
    expect(invalidate).toHaveBeenCalled();
  });

  it("writes the arca host id when a newly-online host appears after connect", async () => {
    vi.useFakeTimers();
    let pushStatus: (s: ArcaStatus) => void = () => {};
    vi.mocked(getArcaStatus).mockResolvedValue(null);
    vi.mocked(onArcaStatusChanged).mockImplementation((cb) => {
      pushStatus = cb;
      return () => {};
    });
    vi.mocked(fetchHosts)
      .mockResolvedValueOnce([]) // snapshot at starting: no online hosts
      .mockResolvedValue([{ host_id: "arca-box", name: "box", owner: "me", status: "online" }]);

    const qc = makeQueryClient();
    renderHook(() => useArcaAutoConnect(), { wrapper: wrapperWith(qc) });
    await act(() => vi.runAllTimersAsync());

    const starting: ArcaStatus = { state: "starting", autoConnect: true, command: null };
    act(() => pushStatus(starting));
    await act(() => vi.runAllTimersAsync());

    const online: ArcaStatus = { state: "online", autoConnect: true, command: null };
    act(() => pushStatus(online));
    await act(() => vi.runAllTimersAsync());

    expect(writeArcaHostId).toHaveBeenCalledWith("arca-box");
  });

  it("shows a persistent error toast on failed (timeout kind)", async () => {
    vi.useFakeTimers();
    let pushStatus: (s: ArcaStatus) => void = () => {};
    vi.mocked(getArcaStatus).mockResolvedValue(null);
    vi.mocked(onArcaStatusChanged).mockImplementation((cb) => {
      pushStatus = cb;
      return () => {};
    });

    const qc = makeQueryClient();
    renderHook(() => useArcaAutoConnect(), { wrapper: wrapperWith(qc) });
    await act(() => vi.runAllTimersAsync());

    const failed: ArcaStatus = {
      state: "failed",
      autoConnect: true,
      command: null,
      errorKind: "timeout",
      error: "Timed out.",
      finishedAt: 1001,
    };
    act(() => pushStatus(failed));
    await act(() => vi.runAllTimersAsync());

    expect(toast.error).toHaveBeenCalledWith(
      "Couldn't connect Arca",
      expect.objectContaining({
        id: "arca-autoconnect",
        duration: Infinity,
        description: "Timed out.",
      }),
    );
  });

  it("does not re-toast the same failed status on remount (same finishedAt)", async () => {
    vi.useFakeTimers();
    const failed: ArcaStatus = {
      state: "failed",
      autoConnect: true,
      command: null,
      errorKind: "unknown",
      error: "Something went wrong.",
      finishedAt: 2000,
    };
    vi.mocked(getArcaStatus).mockResolvedValue(failed);
    vi.mocked(onArcaStatusChanged).mockImplementation(() => () => {});

    const qc = makeQueryClient();
    const { unmount } = renderHook(() => useArcaAutoConnect(), { wrapper: wrapperWith(qc) });
    await act(() => vi.runAllTimersAsync());
    expect(toast.error).toHaveBeenCalledTimes(1);

    // Remount — the same failure (same finishedAt) should not re-toast.
    unmount();
    vi.clearAllMocks();
    vi.mocked(getArcaStatus).mockResolvedValue(failed);
    vi.mocked(onArcaStatusChanged).mockImplementation(() => () => {});
    renderHook(() => useArcaAutoConnect(), { wrapper: wrapperWith(qc) });
    await act(() => vi.runAllTimersAsync());
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("toasts a new failure when finishedAt changes", async () => {
    vi.useFakeTimers();
    let pushStatus: (s: ArcaStatus) => void = () => {};
    vi.mocked(getArcaStatus).mockResolvedValue(null);
    vi.mocked(onArcaStatusChanged).mockImplementation((cb) => {
      pushStatus = cb;
      return () => {};
    });

    const qc = makeQueryClient();
    renderHook(() => useArcaAutoConnect(), { wrapper: wrapperWith(qc) });
    await act(() => vi.runAllTimersAsync());

    const failed1: ArcaStatus = {
      state: "failed",
      autoConnect: true,
      command: null,
      errorKind: "unknown",
      error: "First failure",
      finishedAt: 1000,
    };
    act(() => pushStatus(failed1));
    await act(() => vi.runAllTimersAsync());
    expect(toast.error).toHaveBeenCalledTimes(1);

    // Same toast again with same finishedAt — no new toast.
    act(() => pushStatus(failed1));
    await act(() => vi.runAllTimersAsync());
    expect(toast.error).toHaveBeenCalledTimes(1);

    // New failure with different finishedAt — toast.
    const failed2: ArcaStatus = { ...failed1, finishedAt: 2000, error: "Second failure" };
    act(() => pushStatus(failed2));
    await act(() => vi.runAllTimersAsync());
    expect(toast.error).toHaveBeenCalledTimes(2);
  });
});
