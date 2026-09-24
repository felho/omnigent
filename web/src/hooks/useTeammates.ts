import { useQuery } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";

/** Teammates run inside the harness and have no child-session row. */
export interface TeammateInfo {
  teammate_id: string;
  status: "active" | "idle";
  color: string | null;
  last_summary: string | null;
  last_message_preview: string | null;
}

interface TeammateWire {
  teammate_id: string;
  status?: string;
  color?: string | null;
  last_summary?: string | null;
  last_message_preview?: string | null;
}

interface TeammatesResponse {
  object: "list";
  data: TeammateWire[];
}

export function teammatesQueryKey(conversationId: string): readonly unknown[] {
  return ["conversation", conversationId, "teammates"];
}

export async function fetchTeammates(sessionId: string): Promise<TeammateInfo[]> {
  const res = await authenticatedFetch(`/v1/sessions/${encodeURIComponent(sessionId)}/teammates`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const json = (await res.json()) as TeammatesResponse;
  return json.data.map((row) => ({
    teammate_id: row.teammate_id,
    status: row.status === "idle" ? "idle" : "active",
    color: row.color ?? null,
    last_summary: row.last_summary ?? null,
    last_message_preview: row.last_message_preview ?? null,
  }));
}

interface UseTeammatesResult {
  teammates: TeammateInfo[];
}

/** Poll while mounted: teammate deliveries have no push event yet. */
export function useTeammates(conversationId: string | null): UseTeammatesResult {
  const { data } = useQuery({
    queryKey:
      conversationId === null
        ? ["conversation", null, "teammates"]
        : teammatesQueryKey(conversationId),
    queryFn: () => fetchTeammates(conversationId as string),
    enabled: conversationId !== null,
    retry: false,
    refetchInterval: 10_000,
  });
  return { teammates: data ?? [] };
}
