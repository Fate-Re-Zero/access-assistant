const HOT_TOPICS = [
  "xxx账号为什么登录新百区盟重神兵失败？",
  "调接口报了商户未授权如何处理？",
  "接口报了签名错误，查询一下具体原因？",
  "查询xxx订单号这笔订单的发货情况？",
  "如何使用 /skills 查看可用技能？",
];

const GUIDE_ITEMS = [
  {
    icon: "🔐",
    title: "认证助手",
    subtitle: "登录失败、账号状态、操作记录与加解密排查",
  },
  {
    icon: "💳",
    title: "支付助手",
    subtitle: "解决手游订单发货、支付相关问题",
  },
  {
    icon: "🔗",
    title: "接入助手",
    subtitle: "商户接入、接口联调与配置问题",
  },
  {
    icon: "⚡",
    title: "快捷指令",
    subtitle: "输入 /skills 或 /prompt 查看技能与提示词",
  },
];

type WelcomePanelProps = {
  onPromptSelect: (text: string) => void;
};

export function WelcomePanel({ onPromptSelect }: WelcomePanelProps) {
  return (
    <div className="welcome-panel">
      <div className="welcome-hero">
        <div className="welcome-avatar" aria-hidden="true">
          ✦
        </div>
        <h1 className="welcome-title">你好，我是 Access Assistant</h1>
        <p className="welcome-subtitle">
          支付中心智能助手，支持认证、支付、接入支持等问题解答
        </p>
      </div>

      <div className="welcome-cards">
        <section className="welcome-card">
          <h2 className="welcome-card-title">热门话题</h2>
          <ol className="welcome-topic-list">
            {HOT_TOPICS.map((topic, index) => (
              <li key={topic}>
                <button
                  type="button"
                  className="welcome-topic-item"
                  onClick={() => onPromptSelect(topic)}
                >
                  <span className="welcome-topic-index">{index + 1}</span>
                  <span>{topic}</span>
                </button>
              </li>
            ))}
          </ol>
        </section>

        <section className="welcome-card">
          <h2 className="welcome-card-title">能力指南</h2>
          <ul className="welcome-guide-list">
            {GUIDE_ITEMS.map((item) => (
              <li key={item.title} className="welcome-guide-item">
                <span className="welcome-guide-icon" aria-hidden="true">
                  {item.icon}
                </span>
                <div>
                  <div className="welcome-guide-title">{item.title}</div>
                  <div className="welcome-guide-subtitle">{item.subtitle}</div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
