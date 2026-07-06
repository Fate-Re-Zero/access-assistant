import type { ThreadState } from "../state/chatReducer";

type ThreadGroup = {
  label: string;
  threads: ThreadState[];
};

function getThreadTitle(thread: ThreadState): string {
  const firstUser = thread.timeline.find((entry) => entry.kind === "user");
  if (firstUser && firstUser.kind === "user") {
    const text = firstUser.text.trim();
    if (text) {
      return text.length > 28 ? `${text.slice(0, 28)}…` : text;
    }
  }
  return thread.label || "新对话";
}

function startOfDay(timestamp: number): number {
  const date = new Date(timestamp);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
}

function groupThreadsByDate(
  threadOrder: string[],
  threads: Record<string, ThreadState>,
): ThreadGroup[] {
  const todayStart = startOfDay(Date.now());
  const yesterdayStart = todayStart - 86_400_000;

  const today: ThreadState[] = [];
  const yesterday: ThreadState[] = [];
  const earlier: ThreadState[] = [];

  for (const threadId of [...threadOrder].reverse()) {
    const thread = threads[threadId];
    if (!thread) {
      continue;
    }

    const createdAt = thread.createdAt;
    if (createdAt >= todayStart) {
      today.push(thread);
    } else if (createdAt >= yesterdayStart) {
      yesterday.push(thread);
    } else {
      earlier.push(thread);
    }
  }

  const groups: ThreadGroup[] = [];
  if (today.length > 0) {
    groups.push({ label: "今天", threads: today });
  }
  if (yesterday.length > 0) {
    groups.push({ label: "昨天", threads: yesterday });
  }
  if (earlier.length > 0) {
    groups.push({ label: "更早", threads: earlier });
  }
  return groups;
}

type ConversationSidebarProps = {
  threadOrder: string[];
  threads: Record<string, ThreadState>;
  activeThreadId: string;
  disabled?: boolean;
  onNewThread: () => void;
  onSelectThread: (threadId: string) => void;
};

export function ConversationSidebar({
  threadOrder,
  threads,
  activeThreadId,
  disabled = false,
  onNewThread,
  onSelectThread,
}: ConversationSidebarProps) {
  const groups = groupThreadsByDate(threadOrder, threads);

  return (
    <aside className="conversation-sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-brand-icon" aria-hidden="true">
          ✦
        </span>
        <span className="sidebar-brand-text">Access Assistant</span>
      </div>

      <button
        type="button"
        className="sidebar-new-chat"
        onClick={onNewThread}
        disabled={disabled}
      >
        + 新对话
      </button>

      <nav className="sidebar-thread-list" aria-label="对话历史">
        {groups.length === 0 ? (
          <p className="sidebar-empty">暂无对话记录</p>
        ) : (
          groups.map((group) => (
            <section key={group.label} className="sidebar-thread-group">
              <h3 className="sidebar-group-label">{group.label}</h3>
              <ul className="sidebar-thread-items">
                {group.threads.map((thread) => {
                  const isActive = thread.id === activeThreadId;
                  const title = getThreadTitle(thread);
                  const displayTitle = isActive ? `[当前对话] ${title}` : title;

                  return (
                    <li key={thread.id}>
                      <button
                        type="button"
                        className={`sidebar-thread-item${isActive ? " is-active" : ""}`}
                        onClick={() => onSelectThread(thread.id)}
                        disabled={disabled}
                        title={displayTitle}
                      >
                        {displayTitle}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))
        )}
      </nav>

      <footer className="sidebar-footer">
        <span className="sidebar-footer-avatar" aria-hidden="true">
          A
        </span>
      </footer>
    </aside>
  );
}
