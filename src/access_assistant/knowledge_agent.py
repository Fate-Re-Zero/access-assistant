# """
# Knowledge Agent
# """

# from typing import Annotated

# from typing_extensions import TypedDict

# from langgraph.graph import StateGraph, START, END
# from langgraph.graph.message import add_messages

# import os
# import logging

# from dotenv import load_dotenv
# from langchain_openai import ChatOpenAI


# import json

# from langchain_core.messages import ToolMessage, AIMessage

# from langgraph.prebuilt import ToolNode, tools_condition

# from langgraph.checkpoint.memory import InMemorySaver

# from langgraph.types import Command, interrupt
# from langchain_core.tools import InjectedToolCallId, tool
# from langgraph.graph import StateGraph, START, MessagesState
# from langchain_core.tools import tool, InjectedToolCallId
# from langgraph.prebuilt import InjectedState

# from langgraph.prebuilt import create_react_agent

# from langchain_core.messages import convert_to_messages

# from langchain.chat_models import init_chat_model

# from langgraph.types import Send

# from .agent import AccessAssistantAgent

# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
# )
# log = logging.getLogger(__name__)

# # 加载环境变量（override=True 确保 .env 文件覆盖系统环境变量）
# load_dotenv(override=True)

# # @dataclass(frozen=True)
# # class KnowledgeModelConfig:
# #     """知识库agent模型初始化配置"""

# #     provider: str
# #     model: str
# #     api_key: str | None
# #     base_url: str | None
# #     supports_extended_thinking: bool

# def pretty_print_messages(update, last_message=False):
#     is_subgraph = False
#     if isinstance(update, tuple):
#         ns, update = update
#         # skip parent graph updates in the printouts
#         if len(ns) == 0:
#             return

#         graph_id = ns[-1].split(":")[0]
#         print(f"Update from subgraph {graph_id}:")
#         print("\n")
#         is_subgraph = True

#     for node_name, node_update in update.items():
#         update_label = f"Update from node {node_name}:"
#         if is_subgraph:
#             update_label = "\t" + update_label

#         print(update_label)
#         print("\n")

#         messages = convert_to_messages(node_update["messages"])
#         if last_message:
#             messages = messages[-1:]

#         for m in messages:
#             pretty_print_message(m, indent=is_subgraph)
#         print("\n")

# def pretty_print_message(message, indent=False):
#     pretty_message = message.pretty_repr(html=True)
#     if not indent:
#         print(pretty_message)
#         return

#     indented = "\n".join("\t" + c for c in pretty_message.split("\n"))
#     print(indented)


# def create_task_description_handoff_tool(
#     *, agent_name: str, description: str | None = None
# ):
#     name = f"transfer_to_{agent_name}"
#     description = description or f"Ask {agent_name} for help."

#     @tool(name, description=description)
#     def handoff_tool(
#         # this is populated by the supervisor LLM
#         task_description: Annotated[
#             str,
#             "Description of what the next agent should do, including all of the relevant context.",
#         ],
#         # these parameters are ignored by the LLM
#         state: Annotated[MessagesState, InjectedState],
#     ) -> Command:
#         task_description_message = {"role": "user", "content": task_description}
#         agent_input = {**state, "messages": [task_description_message]}
#         return Command(
#             goto=[Send(agent_name, agent_input)],
#             graph=Command.PARENT,
#         )

#     return handoff_tool


# def create_access_assistant_node(access_assistant_agent: AccessAssistantAgent):
#     """Wrap AccessAssistantAgent as a LangGraph node."""

#     def access_assistant_node(state: MessagesState):
#         result = access_assistant_agent.agent.invoke(
#             {"messages": state["messages"]},
#             config={"configurable": {"thread_id": "access_assistant_subgraph"}},
#             context=access_assistant_agent.context,
#         )

#         last_response = access_assistant_agent.get_last_response(result)
#         if not last_response:
#             return {"messages": []}

#         return {
#             "messages": [
#                 AIMessage(
#                     content=last_response,
#                     name="access_assistant_agent",
#                 )
#             ]
#         }

#     return access_assistant_node


# def main():

#     load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
#     openai_api_key = os.getenv("OPENAI_API_KEY")
#     model_name = os.getenv("DEEPSEEK_MODEL_NAME")
#     openai_base_url = os.getenv("OPENAI_BASE_URL")

#     maxkb_base_url = os.getenv("MAXKB_BASE_URL")


#     llm_kwargs = {
#         "model": model_name,
#         "api_key": openai_api_key,
#         "extra_body": {"thinking": {"type": "disabled"}}
#     }
#     if openai_base_url:
#         llm_kwargs["base_url"] = openai_base_url

#     llm = ChatOpenAI(**llm_kwargs)

#     maxkb_kwargs = {
#         "model": "gpt-3.5-turbo",
#         "api_key": "agent-e4bde99178284424e1e66865875dbfc6",
#         "extra_body": {"thinking": {"type": "disabled"}, "form_data": {"userToken": "lixiang13:696c21730198496e9ed0541de81a2eca"}}
#     }
#     if maxkb_base_url:
#         maxkb_kwargs["base_url"] = maxkb_base_url

#     maxkb = ChatOpenAI(**maxkb_kwargs)

#     # knowledge_agent = create_react_agent(
#     #     model=maxkb,
#     #     tools=[],
#     #     prompt=(
#     #         "你是一个知识库Agent.\n\n"
#     #         "INSTRUCTIONS:\n"
#     #         "- 当用户问题是一个事前问题时，使用知识库Agent回答。\n"
#     #         "- 事前问题通常通常是一些业务问题或者某些业务如何接入，例如：\n"
#     #         "- 统一收银台如何接入？\n"
#     #         "- 游戏外充值如何接入？\n"
#     #         "- 国内游戏实名认证能否用护照证件。"
#     #     ),
#     #     name="knowledge_agent",
#     # )

#     access_assistant_agent = AccessAssistantAgent()
#     access_assistant_node = create_access_assistant_node(access_assistant_agent)

#     # assign_to_knowledge_agent_with_description = create_task_description_handoff_tool(
#     #     agent_name="knowledge_agent",
#     #     description="Assign task to a knowledge agent.",
#     # )
#     assign_to_access_assistant_with_description = create_task_description_handoff_tool(
#         agent_name="access_assistant_agent",
#         description="Assign task to the access assistant for API integration and troubleshooting.",
#     )

#     supervisor_agent_with_description = create_react_agent(
#         model=llm,
#         tools=[
#             # assign_to_knowledge_agent_with_description,
#             assign_to_access_assistant_with_description,
#         ],
#         prompt=(
#             "You are a supervisor managing two agents:\n"
#             # "- a knowledge agent. Assign knowledge-base and pre-sales onboarding tasks to this assistant.\n"
#             "- an access assistant agent. Assign API access, integration, authorization, parameter, signature, permission and troubleshooting tasks to this assistant.\n"
#             "你只需要将用户问题原样交给其他智能体，不要润色其他信息。\n"
#             # "knowledge_agent返回给你的结果，你不需要分析，特别是里面的quick_question你不需要处理，直接将knowledge_agent的结果返回给用户，不要润色。\n"
#             "access_assistant_agent返回给你的结果，你也不需要分析，直接原样返回给用户，不要润色。\n"
#             "Assign work to one agent at a time, do not call agents in parallel.\n"
#             "Do not do any work yourself."
#         ),
#         name="supervisor",
#     )

#     supervisor_with_description = (
#         StateGraph(MessagesState)
#         .add_node(
#             supervisor_agent_with_description,
#             # destinations=("knowledge_agent", "access_assistant_agent"),
#             destinations=("access_assistant_agent",),
#         )
#         # .add_node(knowledge_agent)
#         .add_node("access_assistant_agent", access_assistant_node)
#         .add_edge(START, "supervisor")
#         # .add_edge("knowledge_agent", "supervisor")
#         .add_edge("access_assistant_agent", "supervisor")
#         .compile()
#     )

#     png_data = supervisor_with_description.get_graph().draw_mermaid_png()
#     with open("graph.png", "wb") as f:
#         f.write(png_data)
#     log.info("图已保存到 graph.png")

#     for chunk in supervisor_with_description.stream(
#         {
#             "messages": [
#                 {
#                     "role": "user",
#                     "content": "我调用接口，报了未授权，该如何处理？",
#                 }
#             ]
#         },
#         subgraphs=True,
#     ):
#         pretty_print_messages(chunk, last_message=True)


# if __name__ == "__main__":
#     main()

# # class KnowledgeAgent:
# #     """
# #     使用示例：
# #         agent = KnowledgeAgent()
# #     """

# #     def __init__(
# #         self,
# #         model: Optional[str] = None,
# #         model_provider: Optional[str] = None,
# #         skill_paths: Optional[list[Path]] = None,
# #         working_directory: Optional[Path] = None,
# #         max_tokens: Optional[int] = None,
# #         temperature: Optional[float] = None,
# #         enable_thinking: bool = True,
# #         thinking_budget: int = DEFAULT_THINKING_BUDGET,
# #     ):
# #         """
# #         初始化 Agent

# #         Args:
# #             model: 模型名称，默认 gpt-5.4
# #             model_provider: 模型提供商，支持 anthropic / openai
# #             skill_paths: Skills 搜索路径
# #             working_directory: 工作目录
# #             max_tokens: 最大 tokens
# #             temperature: 温度参数 (启用 thinking 时强制为 1.0)
# #             enable_thinking: 是否启用 Extended Thinking
# #             thinking_budget: thinking 的 token 预算
# #         """

# #         knowledge_model_provider = os.getenv("MODEL_PROVIDER")
# #         knowledge_model_name = os.getenv("MODEL_NAME")
        
# #         self.model_config = resolve_knowledge_model_config()
# #         self.model_provider = self.model_config.provider
# #         self.model_name = self.model_config.model

# #         # thinking 配置
# #         self.enable_thinking = enable_thinking and self.model_config.supports_extended_thinking
# #         self.thinking_budget = thinking_budget

# #         # 配置
# #         self.max_tokens = max_tokens or int(os.getenv("MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
# #         if self.enable_thinking:
# #             self.temperature = 1.0  # Anthropic 要求启用 thinking 时温度为 1.0
# #         else:
# #             self.temperature = (
# #                 temperature
# #                 if temperature is not None
# #                 else float(os.getenv("MODEL_TEMPERATURE", str(DEFAULT_TEMPERATURE)))
# #             )
# #         self.working_directory = working_directory or Path.cwd()

# #         # 初始化 SkillLoader
# #         self.skill_loader = SkillLoader(skill_paths)

# #         self.system_prompt = self._build_system_prompt()

# #         # 创建上下文（供 tools 使用）
# #         self.context = SkillAgentContext(
# #             skill_loader=self.skill_loader,
# #             working_directory=self.working_directory,
# #         )

# #         # 创建 LangChain Agent
# #         self.agent = self._create_agent()

# #     def _build_system_prompt(self) -> str:
# #         """
# #         构建 system prompt

# #         将所有 Skills 的元数据注入到 system prompt。
# #         每个 skill 约 100 tokens，启动时一次性加载。
# #         """
# #         base_prompt = """You are a helpful Access Assistant with access to specialized skills.

# # Your capabilities include:
# # - Loading and using specialized skills for specific tasks
# # - Executing bash commands and scripts
# # - Reading and writing files
# # - Following skill instructions to complete complex tasks

# # When the user asks in Chinese, answer in Chinese.
# # When a user request matches a skill's description, use the load_skill tool to get detailed instructions before proceeding."""

# #         return self.skill_loader.build_system_prompt(base_prompt)


# # def resolve_knowledge_model_config() -> KnowledgeModelConfig:
# #     """解析知识库agent配置"""
# #     knowledge_model_provider = os.getenv("MODEL_PROVIDER")
# #     knowledge_model_name = os.getenv("MODEL_NAME")
# #     knowledge_api_key = os.getenv("MODEL_API_KEY")
# #     knowledge_base_url = os.getenv("MODEL_BASE_URL")

# #     return KnowledgeModelConfig(
# #         provider=knowledge_model_provider,
# #         model=knowledge_model_name,
# #         api_key=knowledge_api_key,
# #         base_url=knowledge_base_url,
# #         supports_extended_thinking=False,
# #     )
