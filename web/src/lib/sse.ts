import type { AgentStreamEvent } from "../types/events";

const DEFAULT_CHAT_STREAM_PATH = "/api/chat/stream";

const STREAM_EVENT_TYPES = [
  "thinking",
  "text",
  "tool_call",
  "tool_result",
  "agent_call",
  "agent_done",
  "done",
  "error",
  "agent_error",
] as const;

type StreamOptions = {
  apiBaseUrl: string;
  message: string;
  threadId: string;
  onEvent: (event: AgentStreamEvent) => void;
  onError: (error: string) => void;
};

type ParsedSseEvent = {
  eventName?: string;
  data: string;
};

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.endsWith("/") ? baseUrl.slice(0, -1) : baseUrl;
}

function parseSseBuffer(buffer: string): { events: ParsedSseEvent[]; rest: string } {
  const events: ParsedSseEvent[] = [];
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";

  for (const block of blocks) {
    if (!block.trim()) {
      continue;
    }

    let eventName: string | undefined;
    const dataLines: string[] = [];

    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }

    if (dataLines.length > 0) {
      events.push({
        eventName,
        data: dataLines.join("\n"),
      });
    }
  }

  return { events, rest };
}

function dispatchSseEvent(
  eventName: string | undefined,
  data: string,
  onEvent: (event: AgentStreamEvent) => void,
): boolean {
  if (!eventName || !STREAM_EVENT_TYPES.includes(eventName as (typeof STREAM_EVENT_TYPES)[number])) {
    return false;
  }

  const payload = JSON.parse(data) as AgentStreamEvent;
  onEvent(payload);
  return payload.type === "done" || payload.type === "error";
}

export function openChatStream({
  apiBaseUrl,
  message,
  threadId,
  onEvent,
  onError,
}: StreamOptions): () => void {
  const streamPath =
    import.meta.env.VITE_CHAT_STREAM_PATH?.trim() || DEFAULT_CHAT_STREAM_PATH;
  const endpoint = new URL(`${normalizeBaseUrl(apiBaseUrl)}${streamPath}`);
  endpoint.searchParams.set("message", message);
  endpoint.searchParams.set("thread_id", threadId);

  const controller = new AbortController();
  let terminalEventHandled = false;

  const run = async () => {
    try {
      const response = await fetch(endpoint.toString(), {
        method: "GET",
        headers: {
          Accept: "text/event-stream",
        },
        signal: controller.signal,
        cache: "no-store",
      });

      if (!response.ok) {
        let detail = "";
        try {
          detail = (await response.text()).trim();
        } catch {
          detail = "";
        }
        const suffix = detail ? `：${detail.slice(0, 200)}` : "";
        onError(`流式接口请求失败（HTTP ${response.status} ${response.statusText}）${suffix}`);
        return;
      }

      if (!response.body) {
        onError("流式接口返回为空，请检查反向代理是否关闭了 SSE 缓冲。");
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }

        buffer += decoder.decode(value, { stream: true });

        const parsed = parseSseBuffer(buffer);
        buffer = parsed.rest;

        for (const item of parsed.events) {
          try {
            if (dispatchSseEvent(item.eventName, item.data, onEvent)) {
              terminalEventHandled = true;
              return;
            }
          } catch (err) {
            const messageFromError = err instanceof Error ? err.message : String(err);
            onError(`解析 SSE 数据失败：${messageFromError}`);
            return;
          }
        }
      }

      if (!terminalEventHandled) {
        onError("SSE 连接已断开，未收到完整响应。请检查后端日志或 Nginx 代理配置。");
      }
    } catch (err) {
      if (controller.signal.aborted) {
        return;
      }
      const messageFromError = err instanceof Error ? err.message : String(err);
      if (/failed to fetch|networkerror|load failed/i.test(messageFromError)) {
        onError(
          `无法连接流式接口（${endpoint.origin}）。请确认 API 地址、CORS 和 HTTPS 配置是否正确。`,
        );
        return;
      }
      onError(`SSE 连接失败：${messageFromError}`);
    }
  };

  void run();

  return () => {
    controller.abort();
  };
}
