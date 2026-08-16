import { queryDatabase } from "@/lib/notion";
import { upsertItems } from "@/lib/cache";

export const dynamic = "force-dynamic";

let lastModified = "";

export async function GET() {
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    async start(controller) {
      const send = (data: string) => {
        controller.enqueue(encoder.encode(`data: ${data}\n\n`));
      };
      send(JSON.stringify({ type: "connected" }));

      const poll = async () => {
        try {
          const items = await queryDatabase(
            undefined,
            [{ timestamp: "last_edited_time", direction: "descending" }],
            20
          );
          if (items.length > 0) {
            const latestModified = items[0].modifiedAt;
            if (latestModified !== lastModified) {
              lastModified = latestModified;
              upsertItems(items);
              send(JSON.stringify({ type: "update", ids: items.map((i) => i.id) }));
            }
          }
        } catch {
          send(JSON.stringify({ type: "error" }));
        }
      };

      // Poll every 5 seconds
      await poll();
      const interval = setInterval(poll, 5000);

      // Clean up on disconnect
      return () => clearInterval(interval);
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Access-Control-Allow-Origin": "*",
    },
  });
}
