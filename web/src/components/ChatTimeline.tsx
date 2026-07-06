import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type {
  AssistantEntry,
  SubAgentRunView,
  SystemEntry,
  TimelineEntry,
  UserEntry,
} from "../state/chatReducer";
import { ToolCallItem } from "./ToolCallItem";

type ChatTimelineProps = {
  entries: TimelineEntry[];
  onToggleToolExpand: (assistantId: string, toolId: string) => void;
};

function phaseLabel(phase: string): string {
  switch (phase) {
    case "waiting":
      return "思考中";
    case "thinking":
      return "规划中";
    case "analyzing":
      return "分析中";
    case "responding":
      return "生成回复";
    case "done":
      return "已完成";
    case "error":
      return "出错";
    default:
      return phase;
  }
}

function showSpinner(phase: string): boolean {
  return (
    phase === "waiting" ||
    phase === "thinking" ||
    phase === "analyzing" ||
    phase === "responding"
  );
}

function subAgentLabel(name: string, title: string): string {
  if (title.trim()) {
    return title;
  }
  switch (name) {
    case "payment":
      return "支付助手";
    case "integration":
      return "接入助手";
    case "auth":
      return "认证助手";
    case "knowledge":
      return "知识助手";
    case "general":
      return "通用助手";
    default:
      return name;
  }
}

function subAgentStatusLabel(status: string): string {
  if (status === "running") {
    return "执行中";
  }
  if (status === "success") {
    return "已完成";
  }
  if (status === "failed") {
    return "失败";
  }
  return status;
}

const UserMessage = memo(function UserMessage({ entry }: { entry: UserEntry }) {
  return (
    <article className="message-row message-row--user">
      <div className="message-bubble message-bubble--user">
        <p>{entry.text}</p>
      </div>
    </article>
  );
});

const SystemMessage = memo(function SystemMessage({ entry }: { entry: SystemEntry }) {
  return (
    <article className="message-row message-row--system">
      <div className="message-bubble message-bubble--system">
        {entry.markdown ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{entry.text}</ReactMarkdown>
        ) : (
          <p>{entry.text}</p>
        )}
      </div>
    </article>
  );
});

const SubAgentRun = memo(function SubAgentRun({
  assistantId,
  run,
  onToggleToolExpand,
}: {
  assistantId: string;
  run: SubAgentRunView;
  onToggleToolExpand: (assistantId: string, toolId: string) => void;
}) {
  return (
    <article className="sub-agent-card">
      <header className="sub-agent-card__header">
        <strong>{subAgentLabel(run.agentName, run.title)}</strong>
        <span className={`sub-agent-pill sub-agent-pill--${run.status}`}>
          {run.status === "running" && <span className="inline-spinner" aria-hidden />}
          {subAgentStatusLabel(run.status)}
        </span>
      </header>

      {run.tools.length > 0 && (
        <section className="panel panel--tools">
          <h4>工具 / MCP 调用 ({run.tools.length})</h4>
          <div className="tools-list">
            {run.tools.map((tool) => (
              <ToolCallItem
                key={tool.id}
                assistantId={assistantId}
                tool={tool}
                onToggleExpand={onToggleToolExpand}
              />
            ))}
          </div>
        </section>
      )}

      {run.thinking && (
        <details className="panel panel--thinking panel--collapsible">
          <summary>思考过程</summary>
          <pre>{run.thinking}</pre>
        </details>
      )}

      {run.response && (
        <details className="panel panel--response panel--collapsible">
          <summary>输出结果</summary>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{run.response}</ReactMarkdown>
        </details>
      )}

      {run.error && <p className="error-text">{run.error}</p>}
    </article>
  );
});

const AssistantMessage = memo(function AssistantMessage({
  entry,
  onToggleToolExpand,
}: {
  entry: AssistantEntry;
  onToggleToolExpand: (assistantId: string, toolId: string) => void;
}) {
  const hasDetails =
    Boolean(entry.thinking) ||
    entry.subAgents.length > 0 ||
    entry.tools.length > 0 ||
    Boolean(entry.error);

  const subAgentToolCount = entry.subAgents.reduce((sum, run) => sum + run.tools.length, 0);
  const totalToolCount = entry.tools.length + subAgentToolCount;
  const hasRunningTools =
    entry.tools.some((tool) => tool.status === "running") ||
    entry.subAgents.some((run) =>
      run.status === "running" || run.tools.some((tool) => tool.status === "running"),
    );
  const shouldAutoOpenDetails =
    totalToolCount > 0 ||
    hasRunningTools ||
    (entry.phase !== "done" &&
      entry.phase !== "error" &&
      (entry.subAgents.length > 0 || Boolean(entry.thinking)));

  return (
    <article className="message-row message-row--assistant">
      <div className="message-avatar" aria-hidden="true">
        ✦
      </div>

      <div className="message-body">
        {entry.phase !== "done" && entry.phase !== "error" && (
          <div className="message-status">
            <span className="phase-pill">
              {showSpinner(entry.phase) && <span className="inline-spinner" aria-hidden />}
              {phaseLabel(entry.phase)}
            </span>
          </div>
        )}

        {entry.response && (
          <div className="message-bubble message-bubble--assistant">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{entry.response}</ReactMarkdown>
          </div>
        )}

        {hasDetails && (
          <details className="message-details" open={shouldAutoOpenDetails}>
            <summary>
              查看执行详情
              {totalToolCount > 0 ? `（${totalToolCount} 个工具/MCP 调用）` : ""}
            </summary>

            {entry.subAgents.length > 0 && (
              <section className="panel panel--subagents">
                <h4>子智能体</h4>
                <div className="sub-agent-list">
                  {entry.subAgents.map((run) => (
                    <SubAgentRun
                      key={run.id}
                      assistantId={entry.id}
                      run={run}
                      onToggleToolExpand={onToggleToolExpand}
                    />
                  ))}
                </div>
              </section>
            )}

            {entry.tools.length > 0 && (
              <section className="panel panel--tools">
                <h4>工具 / MCP 调用</h4>
                <div className="tools-list">
                  {entry.tools.map((tool) => (
                    <ToolCallItem
                      key={tool.id}
                      assistantId={entry.id}
                      tool={tool}
                      onToggleExpand={onToggleToolExpand}
                    />
                  ))}
                </div>
              </section>
            )}

            {entry.thinking && (
              <details className="panel panel--thinking panel--collapsible">
                <summary>任务规划</summary>
                <pre>{entry.thinking}</pre>
              </details>
            )}

            {entry.error && <p className="error-text">{entry.error}</p>}
          </details>
        )}
      </div>
    </article>
  );
});

export const ChatTimeline = memo(function ChatTimeline({ entries, onToggleToolExpand }: ChatTimelineProps) {
  return (
    <section className="chat-timeline" aria-live="polite">
      {entries.map((entry) => {
        if (entry.kind === "user") {
          return <UserMessage key={entry.id} entry={entry} />;
        }

        if (entry.kind === "system") {
          return <SystemMessage key={entry.id} entry={entry} />;
        }

        return (
          <AssistantMessage
            key={entry.id}
            entry={entry}
            onToggleToolExpand={onToggleToolExpand}
          />
        );
      })}
    </section>
  );
});
