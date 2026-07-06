import type {
  AgentStreamEvent,
  AgentCallEvent,
  AgentDoneEvent,
  AgentErrorEvent,
  DoneEvent,
  ErrorEvent,
  ThinkingEvent,
  ToolCallEvent,
  ToolResultEvent,
  TextEvent,
} from "../types/events";

export type RunPhase =
  | "waiting"
  | "thinking"
  | "analyzing"
  | "responding"
  | "done"
  | "error";

export type ToolStatus = "running" | "success" | "failed";
export type SubAgentStatus = "running" | "success" | "failed";

export type SkillMcpSummary = {
  name: string;
  transport?: string;
  url?: string;
  tool_count?: number;
  tools?: string[];
  error?: string | null;
};

export type SkillSummary = {
  name: string;
  description: string;
  path: string;
  agent?: string;
  mcp_servers?: string[];
  mcps?: SkillMcpSummary[];
};

export type ToolCallView = {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: ToolStatus;
  result?: string;
  success?: boolean;
  expanded?: boolean;
  skillName?: string;
};

export type SubAgentRunView = {
  id: string;
  agentName: string;
  title: string;
  status: SubAgentStatus;
  thinking: string;
  response: string;
  tools: ToolCallView[];
  error?: string;
};

export type UserEntry = {
  kind: "user";
  id: string;
  text: string;
  createdAt: number;
};

export type AssistantEntry = {
  kind: "assistant";
  id: string;
  createdAt: number;
  phase: RunPhase;
  thinking: string;
  response: string;
  tools: ToolCallView[];
  subAgents: SubAgentRunView[];
  error?: string;
};

export type SystemEntry = {
  kind: "system";
  id: string;
  text: string;
  markdown: boolean;
  createdAt: number;
};

export type TimelineEntry = UserEntry | AssistantEntry | SystemEntry;

export type ThreadState = {
  id: string;
  label: string;
  createdAt: number;
  timeline: TimelineEntry[];
  activeAssistantEntryId?: string;
  activeSkillName?: string;
};

export type ChatState = {
  skills: SkillSummary[];
  skillsLoaded: boolean;
  skillsError?: string;
  promptCache?: string;
  threads: Record<string, ThreadState>;
  threadOrder: string[];
  activeThreadId: string;
  isStreaming: boolean;
  streamError?: string;
};

export type ChatAction =
  | { type: "skills_loaded"; skills: SkillSummary[] }
  | { type: "skills_failed"; message: string }
  | { type: "prompt_loaded"; prompt: string }
  | { type: "create_thread"; threadId: string; label: string; createdAt?: number }
  | { type: "switch_thread"; threadId: string }
  | {
      type: "submit_user_message";
      threadId: string;
      message: string;
      userEntryId: string;
      assistantEntryId: string;
      createdAt: number;
    }
  | {
      type: "append_system_message";
      threadId: string;
      entryId: string;
      message: string;
      markdown: boolean;
      createdAt: number;
    }
  | {
      type: "stream_event";
      threadId: string;
      assistantEntryId: string;
      event: AgentStreamEvent;
    }
  | {
      type: "toggle_tool_expand";
      threadId: string;
      assistantEntryId: string;
      toolId: string;
    }
  | {
      type: "stream_failed";
      threadId: string;
      assistantEntryId: string;
      message: string;
    };

const DEFAULT_THREAD_ID = "thread-1";

export function createInitialState(): ChatState {
  return {
    skills: [],
    skillsLoaded: false,
    threads: {
      [DEFAULT_THREAD_ID]: {
        id: DEFAULT_THREAD_ID,
        label: "新对话",
        createdAt: Date.now(),
        timeline: [],
      },
    },
    threadOrder: [DEFAULT_THREAD_ID],
    activeThreadId: DEFAULT_THREAD_ID,
    isStreaming: false,
  };
}

function getThread(state: ChatState, threadId: string): ThreadState | undefined {
  return state.threads[threadId];
}

function replaceThread(
  state: ChatState,
  threadId: string,
  thread: ThreadState,
): ChatState {
  return {
    ...state,
    threads: {
      ...state.threads,
      [threadId]: thread,
    },
  };
}

function inferToolSuccess(content: string, explicit?: boolean): boolean {
  if (typeof explicit === "boolean") {
    return explicit;
  }
  return !content.trimStart().startsWith("[FAILED]");
}

function updateAssistantEntry(
  thread: ThreadState,
  assistantEntryId: string,
  updater: (assistant: AssistantEntry) => AssistantEntry,
): ThreadState {
  const nextTimeline = thread.timeline.map((entry) => {
    if (entry.kind !== "assistant" || entry.id !== assistantEntryId) {
      return entry;
    }
    return updater(entry);
  });

  return {
    ...thread,
    timeline: nextTimeline,
  };
}

function upsertToolCall(
  tools: ToolCallView[],
  event: ToolCallEvent,
): { tools: ToolCallView[]; skillName?: string } {
  const args = event.args ?? {};
  const toolId = event.id?.trim() || `${event.name}-${tools.length + 1}`;
  const existingIndex = tools.findIndex((item) => item.id === toolId);

  let skillName: string | undefined;
  if (event.name === "load_skill") {
    const maybeSkill = args["skill_name"];
    if (typeof maybeSkill === "string" && maybeSkill.trim()) {
      skillName = maybeSkill.trim();
    }
  }

  const nextTool: ToolCallView = {
    id: toolId,
    name: event.name,
    args,
    status: "running",
    skillName,
  };

  if (existingIndex >= 0) {
    const cloned = [...tools];
    const original = cloned[existingIndex];
    cloned[existingIndex] = {
      ...original,
      ...nextTool,
      expanded: original.expanded,
      result: original.result,
      success: original.success,
      status: original.status === "running" ? "running" : original.status,
      skillName: skillName ?? original.skillName,
    };
    return { tools: cloned, skillName };
  }

  return { tools: [...tools, nextTool], skillName };
}

function ensureSubAgentRunById(
  subAgents: SubAgentRunView[],
  runId: string | undefined,
  fallback: { agentName?: string; title?: string },
): SubAgentRunView[] {
  if (!runId) {
    return subAgents;
  }
  if (subAgents.some((run) => run.id === runId)) {
    return subAgents;
  }

  const agentName = fallback.agentName?.trim() || "unknown";
  return [
    ...subAgents,
    {
      id: runId,
      agentName,
      title: fallback.title?.trim() || `${agentName} agent`,
      status: "running",
      thinking: "",
      response: "",
      tools: [],
    },
  ];
}

function ensureSubAgentRun(
  subAgents: SubAgentRunView[],
  event: AgentCallEvent,
): SubAgentRunView[] {
  const existingIndex = subAgents.findIndex((item) => item.id === event.id);
  const nextRun: SubAgentRunView = {
    id: event.id,
    agentName: event.agent_name,
    title: event.title || `${event.agent_name} agent`,
    status: "running",
    thinking: "",
    response: "",
    tools: [],
  };

  if (existingIndex >= 0) {
    const cloned = [...subAgents];
    const previous = cloned[existingIndex];
    cloned[existingIndex] = {
      ...previous,
      agentName: event.agent_name || previous.agentName,
      title: event.title || previous.title,
      status: "running",
      tools: previous.tools,
      thinking: previous.thinking,
      response: previous.response,
      error: undefined,
    };
    return cloned;
  }

  return [...subAgents, nextRun];
}

function updateSubAgentRun(
  subAgents: SubAgentRunView[],
  runId: string | undefined,
  updater: (run: SubAgentRunView) => SubAgentRunView,
  fallback?: { agentName?: string; title?: string },
): SubAgentRunView[] {
  if (!runId) {
    return subAgents;
  }

  const ensured = ensureSubAgentRunById(subAgents, runId, fallback ?? {});
  return ensured.map((run) => (run.id === runId ? updater(run) : run));
}

function subAgentFallbackFromEvent(event: {
  agent?: string;
  agent_name?: string;
  task_title?: string;
}): { agentName?: string; title?: string } {
  return {
    agentName: event.agent_name || event.agent,
    title: event.task_title,
  };
}

function applyToolResult(
  tools: ToolCallView[],
  event: ToolResultEvent,
  toolIdPrefix?: string,
): ToolCallView[] {
  const success = inferToolSuccess(event.content, event.success);
  const nextStatus: ToolStatus = success ? "success" : "failed";
  const prefixedId = event.id && toolIdPrefix ? `${toolIdPrefix}:${event.id}` : event.id;

  let index = -1;
  if (prefixedId) {
    index = tools.findIndex((tool) => tool.id === prefixedId);
  }
  if (index < 0) {
    index = tools.findIndex(
      (tool) => tool.status === "running" && tool.name === event.name,
    );
  }

  if (index < 0) {
    index = tools.findIndex((tool) => tool.status === "running");
  }

  if (index < 0) {
    const fallbackId = `${event.name}-${tools.length + 1}`;
    return [
      ...tools,
      {
        id: fallbackId,
        name: event.name,
        args: {},
        status: nextStatus,
        result: event.content,
        success,
        expanded: false,
      },
    ];
  }

  const cloned = [...tools];
  cloned[index] = {
    ...cloned[index],
    status: nextStatus,
    result: event.content,
    success,
  };
  return cloned;
}

function applyThinkingEvent(
  assistant: AssistantEntry,
  event: ThinkingEvent,
): AssistantEntry {
  if (event.agent && event.agent !== "supervisor") {
    return {
      ...assistant,
      phase: "analyzing",
      subAgents: updateSubAgentRun(
        assistant.subAgents,
        event.agent_run_id,
        (run) => ({
          ...run,
          thinking: run.thinking + event.content,
        }),
        subAgentFallbackFromEvent(event),
      ),
    };
  }

  return {
    ...assistant,
    phase: "thinking",
    thinking: assistant.thinking + event.content,
  };
}

function applyTextEvent(
  assistant: AssistantEntry,
  event: TextEvent,
): AssistantEntry {
  if (event.agent && event.agent !== "supervisor") {
    return {
      ...assistant,
      phase: "analyzing",
      subAgents: updateSubAgentRun(
        assistant.subAgents,
        event.agent_run_id,
        (run) => ({
          ...run,
          response: run.response + event.content,
        }),
        subAgentFallbackFromEvent(event),
      ),
    };
  }

  return {
    ...assistant,
    phase: "responding",
    response: assistant.response + event.content,
  };
}

function applyDoneEvent(
  assistant: AssistantEntry,
  event: DoneEvent,
): AssistantEntry {
  return {
    ...assistant,
    phase: "done",
    response: assistant.response || event.response || "",
  };
}

function applyAgentDoneEvent(
  assistant: AssistantEntry,
  event: AgentDoneEvent,
): AssistantEntry {
  return {
    ...assistant,
    phase: "analyzing",
    subAgents: updateSubAgentRun(assistant.subAgents, event.id, (run) => ({
      ...run,
      status: event.success === false ? "failed" : "success",
      response: run.response || event.response || "",
    })),
  };
}

function applyErrorEvent(
  assistant: AssistantEntry,
  event: ErrorEvent,
): AssistantEntry {
  return {
    ...assistant,
    phase: "error",
    error: event.message,
  };
}

function applyAgentErrorEvent(
  assistant: AssistantEntry,
  event: AgentErrorEvent,
): AssistantEntry {
  return {
    ...assistant,
    phase: "analyzing",
    subAgents: updateSubAgentRun(assistant.subAgents, event.id, (run) => ({
      ...run,
      status: "failed",
      error: event.message,
    })),
  };
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "skills_loaded":
      return {
        ...state,
        skills: action.skills,
        skillsLoaded: true,
        skillsError: undefined,
      };

    case "skills_failed":
      return {
        ...state,
        skillsLoaded: true,
        skillsError: action.message,
      };

    case "prompt_loaded":
      return {
        ...state,
        promptCache: action.prompt,
      };

    case "create_thread": {
      if (state.threads[action.threadId]) {
        return {
          ...state,
          activeThreadId: action.threadId,
        };
      }

      return {
        ...state,
        threads: {
          ...state.threads,
          [action.threadId]: {
            id: action.threadId,
            label: action.label,
            createdAt: action.createdAt ?? Date.now(),
            timeline: [],
          },
        },
        threadOrder: [...state.threadOrder, action.threadId],
        activeThreadId: action.threadId,
      };
    }

    case "switch_thread":
      if (!state.threads[action.threadId]) {
        return state;
      }
      return {
        ...state,
        activeThreadId: action.threadId,
      };

    case "submit_user_message": {
      const thread = getThread(state, action.threadId);
      if (!thread) {
        return state;
      }

      const updatedThread: ThreadState = {
        ...thread,
        activeAssistantEntryId: action.assistantEntryId,
        timeline: [
          ...thread.timeline,
          {
            kind: "user",
            id: action.userEntryId,
            text: action.message,
            createdAt: action.createdAt,
          },
          {
            kind: "assistant",
            id: action.assistantEntryId,
            createdAt: action.createdAt,
            phase: "waiting",
            thinking: "",
            response: "",
            tools: [],
            subAgents: [],
          },
        ],
      };

      return {
        ...replaceThread(state, action.threadId, updatedThread),
        isStreaming: true,
        streamError: undefined,
      };
    }

    case "append_system_message": {
      const thread = getThread(state, action.threadId);
      if (!thread) {
        return state;
      }

      const updatedThread: ThreadState = {
        ...thread,
        timeline: [
          ...thread.timeline,
          {
            kind: "system",
            id: action.entryId,
            text: action.message,
            markdown: action.markdown,
            createdAt: action.createdAt,
          },
        ],
      };
      return replaceThread(state, action.threadId, updatedThread);
    }

    case "stream_event": {
      const thread = getThread(state, action.threadId);
      if (!thread) {
        return state;
      }

      let nextThread = thread;
      nextThread = updateAssistantEntry(nextThread, action.assistantEntryId, (assistant) => {
        const event = action.event;
        switch (event.type) {
          case "thinking":
            return applyThinkingEvent(assistant, event);

          case "text":
            return applyTextEvent(assistant, event);

          case "tool_call": {
            if (event.agent && event.agent !== "supervisor") {
              const fallback = subAgentFallbackFromEvent(event);
              return {
                ...assistant,
                phase: "analyzing",
                subAgents: updateSubAgentRun(
                  assistant.subAgents,
                  event.agent_run_id,
                  (run) => {
                    const { tools } = upsertToolCall(run.tools, {
                      ...event,
                      id: event.id ? `${event.agent_run_id}:${event.id}` : event.id,
                    });
                    return {
                      ...run,
                      tools,
                    };
                  },
                  fallback,
                ),
              };
            }

            const { tools } = upsertToolCall(assistant.tools, event);
            return {
              ...assistant,
              tools,
            };
          }

          case "tool_result":
            if (event.agent && event.agent !== "supervisor") {
              const fallback = subAgentFallbackFromEvent(event);
              return {
                ...assistant,
                phase: "analyzing",
                subAgents: updateSubAgentRun(
                  assistant.subAgents,
                  event.agent_run_id,
                  (run) => ({
                    ...run,
                    tools: applyToolResult(run.tools, event, event.agent_run_id),
                  }),
                  fallback,
                ),
              };
            }
            return {
              ...assistant,
              phase: "analyzing",
              tools: applyToolResult(assistant.tools, event),
            };

          case "agent_call":
            return {
              ...assistant,
              phase: "analyzing",
              subAgents: ensureSubAgentRun(assistant.subAgents, event),
            };

          case "agent_done":
            return applyAgentDoneEvent(assistant, event);

          case "agent_error":
            return applyAgentErrorEvent(assistant, event);

          case "done":
            return applyDoneEvent(assistant, event);

          case "error":
            return applyErrorEvent(assistant, event);

          default:
            return assistant;
        }
      });

      if (action.event.type === "tool_call" && action.event.name === "load_skill") {
        const maybeSkill = action.event.args?.["skill_name"];
        if (typeof maybeSkill === "string" && maybeSkill.trim()) {
          nextThread = {
            ...nextThread,
            activeSkillName: maybeSkill.trim(),
          };
        }
      }

      if (action.event.type === "done" || action.event.type === "error") {
        nextThread = {
          ...nextThread,
          activeAssistantEntryId: undefined,
        };
      }

      return {
        ...replaceThread(state, action.threadId, nextThread),
        isStreaming:
          action.event.type === "done" || action.event.type === "error"
            ? false
            : state.isStreaming,
        streamError:
          action.event.type === "error" ? action.event.message : state.streamError,
      };
    }

    case "toggle_tool_expand": {
      const thread = getThread(state, action.threadId);
      if (!thread) {
        return state;
      }

      const updatedThread = updateAssistantEntry(
        thread,
        action.assistantEntryId,
        (assistant) => ({
          ...assistant,
          tools: assistant.tools.map((tool) =>
            tool.id === action.toolId
              ? { ...tool, expanded: !tool.expanded }
              : tool,
          ),
          subAgents: assistant.subAgents.map((run) => ({
            ...run,
            tools: run.tools.map((tool) =>
              tool.id === action.toolId
                ? { ...tool, expanded: !tool.expanded }
                : tool,
            ),
          })),
        }),
      );

      return replaceThread(state, action.threadId, updatedThread);
    }

    case "stream_failed": {
      const thread = getThread(state, action.threadId);
      if (!thread) {
        return {
          ...state,
          isStreaming: false,
          streamError: action.message,
        };
      }

      const updatedThread = updateAssistantEntry(
        thread,
        action.assistantEntryId,
        (assistant) => ({
          ...assistant,
          phase: "error",
          error: action.message,
        }),
      );

      return {
        ...replaceThread(state, action.threadId, {
          ...updatedThread,
          activeAssistantEntryId: undefined,
        }),
        isStreaming: false,
        streamError: action.message,
      };
    }

    default:
      return state;
  }
}
