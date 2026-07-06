export type ThinkingEvent = {
  type: "thinking";
  content: string;
  id?: number;
  agent?: string;
  agent_name?: string;
  agent_run_id?: string;
  task_title?: string;
  route?: string;
  fallback_used?: boolean;
  router_raw_output?: string;
};

export type TextEvent = {
  type: "text";
  content: string;
  agent?: string;
  agent_name?: string;
  agent_run_id?: string;
  task_title?: string;
};

export type ToolCallEvent = {
  type: "tool_call";
  name: string;
  args: Record<string, unknown>;
  id?: string;
  agent?: string;
  agent_name?: string;
  agent_run_id?: string;
  task_title?: string;
};

export type ToolResultEvent = {
  type: "tool_result";
  name: string;
  content: string;
  success?: boolean;
  agent?: string;
  agent_name?: string;
  agent_run_id?: string;
  task_title?: string;
};

export type AgentCallEvent = {
  type: "agent_call";
  id: string;
  agent_name: string;
  title?: string;
};

export type AgentDoneEvent = {
  type: "agent_done";
  id: string;
  agent_name: string;
  response?: string;
  success?: boolean;
};

export type AgentErrorEvent = {
  type: "agent_error";
  id: string;
  agent_name: string;
  message: string;
};

export type DoneEvent = {
  type: "done";
  response?: string;
  agent?: string;
};

export type ErrorEvent = {
  type: "error";
  message: string;
  agent?: string;
};

export type AgentStreamEvent =
  | ThinkingEvent
  | TextEvent
  | ToolCallEvent
  | ToolResultEvent
  | AgentCallEvent
  | AgentDoneEvent
  | AgentErrorEvent
  | DoneEvent
  | ErrorEvent;
