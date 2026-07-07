"""에이전트 실행 엔진 (Agent Execution Engine).

LaarkBlocks 의 핵심 모듈. AgentConfig(설정)를 런타임에 해석하여
LangGraph `StateGraph` 를 동적으로 빌드/컴파일하고, 실행 과정을
정규화된 스트림 이벤트로 방출한다.

그래프 구조 (ReAct 루프)
------------------------
    START ─→ [agent] ─(tool_calls 있음)─→ [tools] ─→ [agent] ─→ ...
                └────(tool_calls 없음)──────────────────────→ END

- agent 노드: 시스템 프롬프트 + 대화 이력을 모델에 전달
- tools 노드: 모델이 요청한 도구를 실행하고 결과를 이력에 추가
- 도구가 없는 에이전트는 agent → END 단일 노드 그래프로 빌드된다.

prebuilt `create_react_agent` 를 쓰지 않고 그래프를 직접 조립하는 이유:
2단계에서 멀티 에이전트(슈퍼바이저), RAG 검색 노드 등 커스텀 노드가
같은 빌더 위에 추가되므로, 처음부터 그래프 조립을 엔진이 소유해야 한다.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from src.core.llm_factory import create_chat_model
from src.core.tool_registry import resolve_tools
from src.core.tracing import TraceCollector, emit
from src.schemas.agent import AgentConfig, StreamEventType
from src.schemas.tracing import SpanType, TraceStatus

logger = logging.getLogger(__name__)

# 에이전트 노드 이름 — 스트림 이벤트 필터링에 사용하므로 상수로 고정
_AGENT_NODE = "agent"
_TOOLS_NODE = "tools"

# 무한 도구 루프 방지용 실행 상한 (그래프 스텝 수 기준)
_RECURSION_LIMIT = 25

# 워커 실행에 붙이는 태그 — 슈퍼바이저의 SSE 스트림에서 워커 내부 이벤트
# (토큰/도구)를 분리하는 기준. 워커 활동은 delegate_to__* 도구의
# tool_start/tool_end 로만 표면화된다. (스트림 이중 출력 방지)
_WORKER_TAG = "laark:worker"


class AgentEngine:
    """설정 → 그래프 동적 빌드 → 스트리밍 실행을 담당하는 실행 엔진.

    세션 메모리 설계
    ----------------
    체크포인터(MemorySaver)는 엔진 수준의 '공유 싱글턴'이다.
    그래프는 요청마다 새로 빌드되지만, 대화 상태는 체크포인터에
    thread_id 별로 저장되므로 그래프 재빌드와 무관하게 이력이 유지된다.

    thread_id 는 "{agent_id}:{session_id}" 로 네임스페이스한다.
    → 같은 session_id 라도 다른 에이전트의 대화와 섞이지 않는다.

    NOTE: MemorySaver 는 인메모리 저장소(프로세스 재시작 시 소멸)로
    개발/데모용이다. 운영 전환(3단계) 시 langgraph-checkpoint-postgres 의
    AsyncPostgresSaver 로 이 필드만 교체하면 된다. (인터페이스 동일)
    """

    def __init__(self) -> None:
        self._checkpointer = MemorySaver()

    @staticmethod
    def _thread_id(config: AgentConfig, session_id: str) -> str:
        """에이전트 간 세션 충돌을 막는 체크포인터 스레드 키."""
        return f"{config.agent_id}:{session_id}"

    def build_graph(
        self, config: AgentConfig, *, use_memory: bool = False
    ) -> CompiledStateGraph:
        """AgentConfig 를 해석해 LangGraph StateGraph 를 빌드/컴파일한다.

        설정 오류(미등록 provider/도구)는 이 단계에서 즉시 예외로 드러나므로,
        스트리밍 시작 전에 검증이 완료되는 효과가 있다.

        Args:
            use_memory: True 면 공유 체크포인터를 결합해 멀티턴 대화를 지원.
                        (session_id 없는 1회성 실행은 False 로 오버헤드 제거)
        """
        # 1) 모델 생성 — provider 추상화 레이어를 통해 인스턴스화
        llm = create_chat_model(config)

        # 2) 도구 해석 — 이름 목록을 실제 구현체로 변환
        tools = resolve_tools(config.tools)

        # 2-1) 슈퍼바이저: 워커들을 위임 도구(delegate_to__*)로 변환해 추가.
        #      워커도 완전한 AgentConfig 이므로 같은 build_graph 로 재귀 빌드된다.
        for worker_cfg in config.workers:
            tools.append(self._make_delegation_tool(worker_cfg))

        # 도구가 있으면 모델에 도구 스키마를 바인딩 (function calling)
        model = llm.bind_tools(tools) if tools else llm

        # 3) agent 노드 정의 — 시스템 프롬프트를 대화 맨 앞에 주입
        system_message = SystemMessage(content=config.system_prompt)

        async def agent_node(state: MessagesState) -> dict[str, Any]:
            """모델 호출 노드. LangGraph 가 astream_events 로 실행하면
            내부의 model.ainvoke 호출도 자동으로 토큰 단위 스트리밍된다."""
            response = await model.ainvoke([system_message, *state["messages"]])
            return {"messages": [response]}

        # 4) 그래프 조립
        builder = StateGraph(MessagesState)
        builder.add_node(_AGENT_NODE, agent_node)
        builder.add_edge(START, _AGENT_NODE)

        if tools:
            # ReAct 루프: 모델 응답에 tool_calls 가 있으면 tools 노드로,
            # 없으면 END 로 분기한다. (tools_condition 이 이 분기를 담당)
            builder.add_node(_TOOLS_NODE, ToolNode(tools))
            builder.add_conditional_edges(_AGENT_NODE, tools_condition)
            builder.add_edge(_TOOLS_NODE, _AGENT_NODE)
        else:
            # 도구 없는 에이전트: 단일 응답 후 종료
            builder.add_edge(_AGENT_NODE, END)

        # 세션 메모리: 공유 체크포인터를 결합하면 thread_id 별로
        # 대화 상태(messages)가 저장/복원된다.
        return builder.compile(
            checkpointer=self._checkpointer if use_memory else None
        )

    def _make_delegation_tool(self, worker_cfg: AgentConfig) -> StructuredTool:
        """워커 에이전트를 슈퍼바이저용 위임 도구로 래핑한다. (agent-as-tool)

        - 도구 description 에 워커의 역할을 담아 슈퍼바이저(모델)의
          라우팅 판단 근거로 제공한다.
        - 워커 실행에 _WORKER_TAG 를 붙여, 슈퍼바이저 스트림에서 워커의
          내부 토큰/도구 이벤트를 걸러낼 수 있게 한다.
        """
        worker_graph = self.build_graph(worker_cfg)  # 워커는 무상태 실행

        async def _delegate(task: str) -> str:
            result = await worker_graph.ainvoke(
                {"messages": [HumanMessage(content=task)]},
                config={
                    "recursion_limit": _RECURSION_LIMIT,
                    "tags": [_WORKER_TAG],
                },
            )
            # 워커의 최종 응답(마지막 AI 메시지)만 슈퍼바이저에게 반환
            for message in reversed(result["messages"]):
                if isinstance(message, AIMessage) and message.content:
                    return _chunk_to_text(message)
            return "(워커가 응답을 생성하지 못했습니다)"

        return StructuredTool.from_function(
            coroutine=_delegate,
            name=f"delegate_to__{worker_cfg.agent_id}",
            description=(
                f"'{worker_cfg.name}' 에이전트에게 작업을 위임한다. "
                f"담당 역할: {worker_cfg.description or worker_cfg.name}. "
                "task 에는 위임할 작업을 완결된 문장으로 구체적으로 서술할 것."
            ),
        )

    async def astream(
        self,
        config: AgentConfig,
        user_message: str,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """에이전트를 실행하며 정규화된 스트림 이벤트를 방출한다.

        LangGraph 의 저수준 이벤트(astream_events)를 클라이언트 친화적인
        LaarkBlocks 이벤트 계약(StreamEventType)으로 변환한다.
        운영 콘솔(4단계)의 트레이싱도 이 이벤트 스트림을 소비하게 된다.

        Args:
            session_id: 지정 시 해당 세션의 이전 대화 맥락 위에서 실행된다.
                        (미지정 시 1회성 무상태 실행)

        Yields:
            {"type": StreamEventType, ...payload} 형태의 dict
        """
        collector = TraceCollector(config, user_message, session_id)
        final_text_parts: list[str] = []  # done 이벤트에 담을 전체 응답 누적 버퍼
        last_model_text = ""  # 스트리밍 미지원 모델 대비 폴백 (on_chat_model_end)

        try:
            # 그래프 빌드 오류(미등록 provider/도구)도 트레이스에 남도록 try 안에서 빌드
            use_memory = session_id is not None
            graph = self.build_graph(config, use_memory=use_memory)
            inputs = {"messages": [HumanMessage(content=user_message)]}

            # 체크포인터는 run config 의 thread_id 를 기준으로 상태를 저장/복원한다
            run_config: dict[str, Any] = {"recursion_limit": _RECURSION_LIMIT}
            if use_memory:
                run_config["configurable"] = {
                    "thread_id": self._thread_id(config, session_id)
                }

            async for event in graph.astream_events(
                inputs,
                version="v2",
                config=run_config,
            ):
                kind = event["event"]
                run_id = str(event.get("run_id"))
                is_worker = _WORKER_TAG in event.get("tags", [])

                # ── 트레이싱 (워커 포함 — 멀티 에이전트 총비용 누락 방지) ──
                if kind == "on_chat_model_start":
                    collector.start_span(
                        run_id,
                        SpanType.LLM,
                        # provider 가 메타데이터로 알려주는 실제 모델명 우선
                        # (워커는 슈퍼바이저와 다른 모델일 수 있다)
                        name=event.get("metadata", {}).get("ls_model_name")
                        or config.model_name,
                        input_value=event["data"].get("input"),
                        is_worker=is_worker,
                    )
                elif kind == "on_chat_model_end":
                    output = event["data"].get("output")
                    collector.end_span(
                        run_id,
                        output_value=(
                            _chunk_to_text(output)
                            if isinstance(output, AIMessage)
                            else output
                        ),
                        # usage_metadata: {"input_tokens": n, "output_tokens": n, ...}
                        usage=getattr(output, "usage_metadata", None),
                        model_name=event.get("metadata", {}).get("ls_model_name"),
                    )
                elif kind == "on_tool_start":
                    collector.start_span(
                        run_id,
                        SpanType.TOOL,
                        name=event.get("name", ""),
                        input_value=event["data"].get("input"),
                        is_worker=is_worker,
                    )
                elif kind == "on_tool_end":
                    collector.end_span(
                        run_id,
                        output_value=_tool_output_to_text(event["data"].get("output")),
                    )

                # ── SSE 방출 — 워커 내부 이벤트는 스킵 (트레이스에만 기록됨).
                #    워커 활동은 delegate_to__* 도구 호출로만 표면화된다.
                if is_worker:
                    continue

                # ── 모델 토큰 스트림 ────────────────────────────
                if kind == "on_chat_model_stream":
                    # agent 노드에서 발생한 청크만 통과시킨다.
                    if event.get("metadata", {}).get("langgraph_node") != _AGENT_NODE:
                        continue
                    text = _chunk_to_text(event["data"]["chunk"])
                    # tool_call 전용 청크는 content 가 비어 있으므로 스킵
                    if not text:
                        continue
                    final_text_parts.append(text)
                    yield {"type": StreamEventType.TOKEN, "content": text}

                # ── 모델 호출 종료 — 비스트리밍 모델 폴백용 최종 텍스트 ──
                elif kind == "on_chat_model_end":
                    if event.get("metadata", {}).get("langgraph_node") == _AGENT_NODE:
                        output = event["data"].get("output")
                        if isinstance(output, AIMessage):
                            last_model_text = _chunk_to_text(output)

                # ── 도구 호출 시작/종료 ─────────────────────────
                elif kind == "on_tool_start":
                    yield {
                        "type": StreamEventType.TOOL_START,
                        "tool": event.get("name", ""),
                        "input": event["data"].get("input"),
                    }
                elif kind == "on_tool_end":
                    yield {
                        "type": StreamEventType.TOOL_END,
                        "tool": event.get("name", ""),
                        "output": _tool_output_to_text(event["data"].get("output")),
                    }

            # ── 정상 종료 ──────────────────────────────────────
            # 토큰 스트림이 없었던 경우(비스트리밍 provider) 마지막 응답으로 폴백
            final_text = "".join(final_text_parts) or last_model_text
            yield {
                "type": StreamEventType.DONE,
                "agent_id": config.agent_id,
                "session_id": session_id,
                "content": final_text,
            }
            collector.finish(TraceStatus.SUCCESS, final_response=final_text)

        except Exception as e:
            # 오류도 트레이스로 남긴다 (운영 콘솔의 '에러 상황 분석' 데이터)
            collector.finish(
                TraceStatus.ERROR,
                final_response="".join(final_text_parts) or last_model_text,
                error=f"{type(e).__name__}: {e}",
            )
            raise

        finally:
            # 클라이언트 연결 종료(GeneratorExit) 등으로 finish 없이 끝나면
            # build() 가 ABORTED 로 확정한다. 적재는 백그라운드 — 응답 지연 0.
            emit(collector.build())

    async def get_history(
        self, config: AgentConfig, session_id: str
    ) -> list[dict[str, str]]:
        """세션의 대화 이력을 조회한다. (운영 콘솔/디버깅용)

        체크포인터에서 직접 최신 체크포인트를 읽으므로 그래프 실행이 필요 없다.
        """
        checkpoint_tuple = await self._checkpointer.aget_tuple(
            {"configurable": {"thread_id": self._thread_id(config, session_id)}}
        )
        if checkpoint_tuple is None:
            return []

        messages: list[BaseMessage] = (
            checkpoint_tuple.checkpoint.get("channel_values", {}).get("messages", [])
        )
        return [_message_to_dict(m) for m in messages]


def _message_to_dict(message: BaseMessage) -> dict[str, str]:
    """LangChain 메시지를 API 응답용 dict 로 변환."""
    role_map = {HumanMessage: "user", AIMessage: "assistant", ToolMessage: "tool"}
    role = next(
        (r for cls, r in role_map.items() if isinstance(message, cls)), "system"
    )
    content = message.content if isinstance(message.content, str) else str(message.content)
    return {"role": role, "content": content}


def _chunk_to_text(chunk: Any) -> str:
    """모델 스트림 청크에서 텍스트만 추출.

    provider 마다 content 형태가 다르다:
      - Ollama/OpenAI: str
      - Anthropic: [{"type": "text", "text": ...}, ...] 블록 리스트
    이 함수가 그 차이를 흡수하여 엔진 밖으로는 항상 str 을 내보낸다.
    """
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _tool_output_to_text(output: Any) -> str:
    """도구 실행 결과를 직렬화 가능한 텍스트로 정규화.

    ToolNode 의 출력은 ToolMessage 인 경우가 많으므로 content 를 우선 사용.
    """
    if output is None:
        return ""
    content = getattr(output, "content", output)
    return content if isinstance(content, str) else str(content)


# 엔진은 상태를 가지지 않으므로(무상태) 모듈 수준 싱글턴으로 공유한다.
engine = AgentEngine()
