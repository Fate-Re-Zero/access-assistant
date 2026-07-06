import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { ChatTimeline } from "./components/ChatTimeline";
import { Composer } from "./components/Composer";
import { ConversationSidebar } from "./components/ConversationSidebar";
import { WelcomePanel } from "./components/WelcomePanel";
import { openChatStream } from "./lib/sse";
import {
  chatReducer,
  createInitialState,
  type SkillSummary,
} from "./state/chatReducer";
import type { AgentStreamEvent } from "./types/events";
import "./App.css";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.trim() || "http://localhost:8000";
  // import.meta.env.VITE_API_BASE_URL?.trim() || "https://access-assistant.u.sdo.com";

function makeId(prefix: string): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function skillsAsMarkdown(skills: SkillSummary[]): string {
  if (!skills.length) {
    return "No skills discovered.";
  }

  return [
    "## Available Skills",
    ...skills.map(
      (skill) =>
        `- **${skill.name}**: ${skill.description || "No description"}\n  - path: \`${skill.path}\``,
    ),
  ].join("\n");
}

function promptAsMarkdown(prompt: string): string {
  const escaped = prompt.replaceAll("```", "` ` `");
  return `## System Prompt\n\n\`\`\`text\n${escaped}\n\`\`\``;
}

const STREAM_PRIORITY_EVENTS = new Set([
  "tool_call",
  "tool_result",
  "agent_call",
  "agent_done",
  "agent_error",
  "done",
  "error",
]);

export default function App() {
  const [state, dispatch] = useReducer(chatReducer, undefined, createInitialState);
  const streamCloserRef = useRef<(() => void) | null>(null);
  const streamBatchRef = useRef<{
    threadId: string;
    assistantEntryId: string;
    pending: Map<string, AgentStreamEvent>;
    frameId: number | null;
  } | null>(null);
  const [composerDraft, setComposerDraft] = useState<string | undefined>(undefined);

  const activeThread = state.threads[state.activeThreadId];
  const timeline = activeThread?.timeline ?? [];
  const isEmpty = timeline.length === 0;

  useEffect(() => {
    let cancelled = false;

    const loadSkills = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/skills`);
        if (!response.ok) {
          throw new Error(`Failed to load skills (${response.status})`);
        }
        const payload = (await response.json()) as { skills: SkillSummary[] };
        if (!cancelled) {
          dispatch({ type: "skills_loaded", skills: payload.skills || [] });
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!cancelled) {
          dispatch({ type: "skills_failed", message });
        }
      }
    };

    loadSkills();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return () => {
      streamCloserRef.current?.();
    };
  }, []);

  const appendSystemMessage = (content: string, markdown = true) => {
    dispatch({
      type: "append_system_message",
      threadId: state.activeThreadId,
      entryId: makeId("system"),
      message: content,
      markdown,
      createdAt: Date.now(),
    });
  };

  const handleSend = async (text: string) => {
    if (state.isStreaming) {
      return;
    }

    if (text === "/skills") {
      appendSystemMessage(skillsAsMarkdown(state.skills));
      return;
    }

    if (text === "/prompt") {
      try {
        const response = await fetch(`${API_BASE_URL}/api/prompt`);
        if (!response.ok) {
          throw new Error(`Failed to load system prompt (${response.status})`);
        }
        const payload = (await response.json()) as { prompt: string };
        dispatch({ type: "prompt_loaded", prompt: payload.prompt || "" });
        appendSystemMessage(promptAsMarkdown(payload.prompt || ""));
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        appendSystemMessage(`Error: ${message}`, false);
      }
      return;
    }

    const threadId = state.activeThreadId;
    const userEntryId = makeId("user");
    const assistantEntryId = makeId("assistant");

    dispatch({
      type: "submit_user_message",
      threadId,
      message: text,
      userEntryId,
      assistantEntryId,
      createdAt: Date.now(),
    });

    streamCloserRef.current?.();
    streamBatchRef.current = {
      threadId,
      assistantEntryId,
      pending: new Map(),
      frameId: null,
    };

    const flushStreamBatch = () => {
      const batch = streamBatchRef.current;
      if (!batch) {
        return;
      }
      batch.frameId = null;
      for (const event of batch.pending.values()) {
        dispatch({
          type: "stream_event",
          threadId: batch.threadId,
          assistantEntryId: batch.assistantEntryId,
          event,
        });
      }
      batch.pending.clear();
    };

    const scheduleStreamBatchFlush = () => {
      const batch = streamBatchRef.current;
      if (!batch || batch.frameId !== null) {
        return;
      }
      batch.frameId = requestAnimationFrame(flushStreamBatch);
    };

    streamCloserRef.current = openChatStream({
      apiBaseUrl: API_BASE_URL,
      message: text,
      threadId,
      onEvent: (event: AgentStreamEvent) => {
        const batch = streamBatchRef.current;
        if (!batch) {
          return;
        }

        if (STREAM_PRIORITY_EVENTS.has(event.type)) {
          flushStreamBatch();
          dispatch({
            type: "stream_event",
            threadId: batch.threadId,
            assistantEntryId: batch.assistantEntryId,
            event,
          });
          if (event.type === "done" || event.type === "error") {
            streamBatchRef.current = null;
            streamCloserRef.current = null;
          }
          return;
        }

        if (event.type === "text" || event.type === "thinking") {
          const key = `${event.type}:${event.agent ?? "supervisor"}:${event.agent_run_id ?? ""}`;
          const previous = batch.pending.get(key);
          if (previous && (previous.type === "text" || previous.type === "thinking")) {
            batch.pending.set(key, {
              ...previous,
              content: `${previous.content}${event.content}`,
            });
          } else {
            batch.pending.set(key, event);
          }
          scheduleStreamBatchFlush();
          return;
        }

        flushStreamBatch();
        dispatch({
          type: "stream_event",
          threadId: batch.threadId,
          assistantEntryId: batch.assistantEntryId,
          event,
        });
      },
      onError: (message) => {
        flushStreamBatch();
        streamBatchRef.current = null;
        dispatch({
          type: "stream_failed",
          threadId,
          assistantEntryId,
          message,
        });
        streamCloserRef.current = null;
      },
    });
  };

  const handleToggleToolExpand = useCallback(
    (assistantId: string, toolId: string) => {
      dispatch({
        type: "toggle_tool_expand",
        threadId: state.activeThreadId,
        assistantEntryId: assistantId,
        toolId,
      });
    },
    [state.activeThreadId],
  );

  const createThread = () => {
    if (state.isStreaming) {
      return;
    }
    const threadNumber = state.threadOrder.length + 1;
    const threadId = `thread-${threadNumber}`;
    dispatch({
      type: "create_thread",
      threadId,
      label: "新对话",
    });
  };

  const handleSelectThread = useCallback(
    (threadId: string) => {
      dispatch({ type: "switch_thread", threadId });
    },
    [],
  );

  const handlePromptSelect = useCallback((text: string) => {
    setComposerDraft(text);
  }, []);

  const clearComposerDraft = useCallback(() => {
    setComposerDraft(undefined);
  }, []);

  return (
    <div className="app-layout">
      <ConversationSidebar
        threadOrder={state.threadOrder}
        threads={state.threads}
        activeThreadId={state.activeThreadId}
        disabled={state.isStreaming}
        onNewThread={createThread}
        onSelectThread={handleSelectThread}
      />

      <main className="main-panel">
        <div className="main-content">
          {isEmpty ? (
            <WelcomePanel onPromptSelect={handlePromptSelect} />
          ) : (
            <ChatTimeline
              entries={timeline}
              onToggleToolExpand={handleToggleToolExpand}
            />
          )}
        </div>

        {state.streamError && <p className="global-error">{state.streamError}</p>}

        <Composer
          disabled={state.isStreaming}
          draft={composerDraft}
          onDraftApplied={clearComposerDraft}
          onSubmit={handleSend}
        />
      </main>
    </div>
  );
}
