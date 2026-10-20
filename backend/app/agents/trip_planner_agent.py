from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.agents.tools.rag_tool import get_destination_guide_context
from app.agents.tools.web_tools import WEB_TOOL_SCHEMAS, execute_web_tool, web_fetch_many
from app.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_RETRIES,
    LLM_MODEL,
    LLM_TIMEOUT_SECONDS,
    TAVILY_API_KEY,
    WEB_AGENT_MAX_STEPS,
)
from app.models.schemas import DayPlan, TripEditRequest, TripRequest


class PlannerDayDraft(BaseModel):
    """LLM 返回的单日最小行程草稿。"""

    day_index: int = Field(..., ge=1)
    theme: str = Field(..., description="当天的简短主题")
    spot_name: str = Field(..., description="当天主要景点名称")
    spot_description: str = Field(..., description="推荐该景点的简短理由")
    meal_name: str = Field(..., description="当天的餐饮或餐厅建议")
    meal_notes: str = Field(..., description="简短的用餐说明")
    daily_note: str = Field(..., description="当天的一条简短规划备注")


class PlannerDraft(BaseModel):
    """提供给 trip_service.py 使用的结构化行程草稿。"""

    summary: str = Field(..., description="整趟旅行的简短概述")
    tips: list[str] = Field(default_factory=list, description="旅行提示")
    days: list[PlannerDayDraft] = Field(default_factory=list)
    research_sources: list[str] = Field(default_factory=list, exclude=True)
    agent_trace: dict = Field(default_factory=dict, exclude=True)


class DayEditDraft(BaseModel):
    """LLM 返回的单日编辑草稿。"""

    theme: str = Field(..., description="编辑后的当天主题")
    spot_name: str = Field(..., description="编辑后的主要景点名称")
    spot_description: str = Field(..., description="编辑后的景点说明")
    meal_name: str = Field(..., description="编辑后的餐饮名称")
    meal_notes: str = Field(..., description="编辑后的餐饮说明")
    daily_note: str = Field(..., description="编辑后的当天备注")
    accepted: bool = Field(default=True, description="用户要求是否合理且可执行")
    decision_reason: str = Field(default="", description="接受或拒绝用户要求的原因")
    alternative_suggestion: str = Field(default="", description="拒绝时提供的可行替代建议")
    research_sources: list[str] = Field(default_factory=list, exclude=True)
    agent_trace: dict = Field(default_factory=dict, exclude=True)


def _message_to_trace(message) -> dict:
    """把 LangChain 或 role/content tuple 转成便于查看的消息记录。"""
    if isinstance(message, tuple) and len(message) == 2:
        role, content = message
        role = {"human": "user", "ai": "assistant"}.get(str(role), str(role))
        return {"role": role, "content": str(content)}
    role = getattr(message, "type", "assistant")
    role = {"human": "user", "ai": "assistant"}.get(role, role)
    record = {"role": role, "content": getattr(message, "content", "")}
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        record["tool_calls"] = [
            {
                "id": call.get("id", ""),
                "type": "function",
                "function": {
                    "name": call.get("name", ""),
                    "arguments": call.get("args", {}),
                },
            }
            for call in tool_calls
        ]
    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id:
        record["tool_call_id"] = tool_call_id
    return record


def _invoke_with_web_tools(llm, messages, required_search_query: str) -> tuple[object, dict[str, int], list[str], dict]:
    """每次强制由模型先发起 Search，再通过 ToolMessage 执行和回传结果。"""
    trace = {"tool_calls": [], "messages": []}
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    sources_by_url: dict[str, str] = {}
    search_status = "[在线检索状态] web_search：未执行"
    fetch_count = 0

    if not TAVILY_API_KEY:
        response = llm.invoke(messages)
        trace["messages"] = [_message_to_trace(message) for message in messages]
        trace["messages"].append(_message_to_trace(response))
        return response, _extract_token_usage(response), ["[在线检索状态] web_search：未执行（未配置 Tavily API Key）", "[在线检索状态] web_fetch：未执行"], trace

    try:
        from langchain_core.messages import ToolMessage
        forced_search_llm = llm.bind_tools(
            WEB_TOOL_SCHEMAS,
            tool_choice={"type": "function", "function": {"name": "web_search"}},
        )
        agent_llm = llm.bind_tools(WEB_TOOL_SCHEMAS)
    except Exception as exc:
        print(f"[trip_planner_agent] 工具调用绑定失败: {type(exc).__name__}: {exc}")
        response = llm.invoke(messages)
        trace["messages"] = [_message_to_trace(message) for message in messages]
        trace["messages"].append(_message_to_trace(response))
        trace["error"] = f"工具调用绑定失败：{type(exc).__name__}: {exc}"
        return response, _extract_token_usage(response), ["[在线检索状态] web_search：未执行（模型不支持强制工具调用）", "[在线检索状态] web_fetch：未执行"], trace

    history = list(messages)
    allowed_fetch_urls: set[str] = set()
    max_steps = max(2, WEB_AGENT_MAX_STEPS)

    for step in range(max_steps):
        try:
            # 首轮强制搜索，确保即使本地 RAG 有材料也会在线检索。
            response = (forced_search_llm if step == 0 else agent_llm).invoke(history)
        except Exception as exc:
            print(f"[trip_planner_agent] 工具调用请求失败: {type(exc).__name__}: {exc}")
            trace["error"] = f"模型工具调用失败：{type(exc).__name__}: {exc}"
            response = llm.invoke(history)
            usage_step = _extract_token_usage(response)
            usage["prompt_tokens"] += usage_step["prompt_tokens"]
            usage["completion_tokens"] += usage_step["completion_tokens"]
            history.append(response)
            trace["messages"] = [_message_to_trace(message) for message in history]
            fetch_status = f"[在线检索状态] web_fetch：成功提取 {fetch_count} 篇正文" if fetch_count else "[在线检索状态] web_fetch：未成功提取正文"
            return response, usage, [search_status, fetch_status] + list(sources_by_url.values()), trace

        usage_step = _extract_token_usage(response)
        usage["prompt_tokens"] += usage_step["prompt_tokens"]
        usage["completion_tokens"] += usage_step["completion_tokens"]
        history.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            if step == 0:
                search_status = "[在线检索状态] web_search：未执行（模型未返回 tool_call）"
            fetch_status = f"[在线检索状态] web_fetch：成功提取 {fetch_count} 篇正文" if fetch_count else "[在线检索状态] web_fetch：未成功提取正文"
            trace["messages"] = [_message_to_trace(message) for message in history]
            return response, usage, [search_status, fetch_status] + list(sources_by_url.values()), trace

        for call in tool_calls:
            name = str(call.get("name", ""))
            arguments = call.get("args", {})
            call_id = str(call.get("id", ""))
            try:
                content, found_sources = execute_web_tool(
                    name,
                    arguments,
                    allowed_urls=allowed_fetch_urls if name == "web_fetch" else None,
                )
                if name == "web_search":
                    allowed_fetch_urls.update(source.get("url", "") for source in found_sources if source.get("url"))
                    search_status = f"[在线检索状态] web_search：已执行，命中 {len(found_sources)} 条公开网页"
                if name == "web_fetch" and found_sources:
                    fetch_count += len(found_sources)
                record = {
                    "origin": "agent",
                    "step": len(trace["tool_calls"]) + 1,
                    "tool_call_id": call_id,
                    "name": name,
                    "arguments": arguments,
                    "status": "success" if found_sources or (name == "web_search" and '"results"' in content) else "no_content",
                    "result": content,
                }
                trace["tool_calls"].append(record)
                for source in found_sources:
                    url = source.get("url", "")
                    if url:
                        title = source.get("title") or "网页"
                        published = source.get("published_date")
                        suffix = f"；发布于 {published}" if published else ""
                        retrieved = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        sources_by_url.setdefault(url, f"在线资料来源：{title} — {url}{suffix}；检索日期 {retrieved}")
                print(f"[trip_planner_agent] tool_call complete: {name}, id={call_id}, status={record['status']}")
            except Exception as exc:
                content = json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, ensure_ascii=False)
                trace["tool_calls"].append({
                    "origin": "agent",
                    "step": len(trace["tool_calls"]) + 1,
                    "tool_call_id": call_id,
                    "name": name,
                    "arguments": arguments,
                    "status": "error",
                    "result": content,
                })
                print(f"[trip_planner_agent] tool_error: {name}: {type(exc).__name__}: {exc}")
            history.append(ToolMessage(content=content, tool_call_id=call_id))

    # 达到 max_steps 后再进行一次不带工具的最终回答，避免丢失用户结果。
    response = llm.invoke(history)
    usage_step = _extract_token_usage(response)
    usage["prompt_tokens"] += usage_step["prompt_tokens"]
    usage["completion_tokens"] += usage_step["completion_tokens"]
    history.append(response)
    trace["messages"] = [_message_to_trace(message) for message in history]
    fetch_status = f"[在线检索状态] web_fetch：成功提取 {fetch_count} 篇正文" if fetch_count else "[在线检索状态] web_fetch：未成功提取正文"
    return response, usage, [search_status, fetch_status] + list(sources_by_url.values()), trace

def _normalize_day_edit_payload(payload: dict) -> dict:
    """兼容模型返回的两种单日编辑格式。"""
    if "spot_name" in payload and "meal_name" in payload and "daily_note" in payload:
        return payload

    normalized = dict(payload)

    spots = payload.get("spots")
    if isinstance(spots, list) and spots:
        first_spot = spots[0] or {}
        normalized.setdefault("spot_name", first_spot.get("name", ""))
        normalized.setdefault("spot_description", first_spot.get("description", ""))

    meals = payload.get("meals")
    if isinstance(meals, list) and meals:
        first_meal = meals[0] or {}
        normalized.setdefault("meal_name", first_meal.get("name", ""))
        normalized.setdefault("meal_notes", first_meal.get("notes", ""))

    notes = payload.get("notes")
    if isinstance(notes, list) and notes:
        normalized.setdefault("daily_note", notes[-1] or "")

    return normalized


def _extract_json_object(raw_text: str) -> str | None:
    """从模型原始文本中尽量提取 JSON 对象字符串。"""
    text = raw_text.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    start_index = text.find("{")
    end_index = text.rfind("}")
    if start_index == -1 or end_index == -1 or end_index <= start_index:
        return None

    return text[start_index : end_index + 1]


def collect_trip_context(
    destination: str,
    preferences: list[str] | None = None,
    pace: str | None = None,
    special_notes: str | None = None,
    top_k: int = 5,
) -> tuple[list[str], dict[str, int], dict[str, int], dict[str, int]]:
    """收集本地攻略片段。返回 (contexts, rewrite_usage, rerank_usage, embedding_usage)。"""
    return get_destination_guide_context(
        destination=destination,
        preferences=preferences,
        pace=pace,
        special_notes=special_notes,
        top_k=top_k,
    )


def _build_chat_llm():
    """创建通用 ChatOpenAI 实例。"""
    if not LLM_API_KEY:
        return None

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        return None

    return ChatOpenAI(
        model=LLM_MODEL,
        temperature=0.3,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL or None,
        timeout=LLM_TIMEOUT_SECONDS,
        max_retries=LLM_MAX_RETRIES,
    )


def _extract_token_usage(response) -> dict[str, int]:
    """从 LangChain AIMessage 中提取 token 使用量。"""
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    metadata = getattr(response, "response_metadata", None) or {}
    token_usage = metadata.get("token_usage", {})
    if token_usage:
        usage["prompt_tokens"] = token_usage.get("prompt_tokens", 0)
        usage["completion_tokens"] = token_usage.get("completion_tokens", 0)
    return usage


def generate_planner_draft(
    request: TripRequest,
    rag_contexts: list[str],
    day_count: int,
) -> tuple[PlannerDraft | None, dict[str, int], dict]:
    """
    使用 LangChain 生成结构化行程草稿。返回 (draft, token_usage)。

    如果当前环境还没有准备好模型调用条件，就返回 None，
    这样 service 层还能回退到规则版实现。
    """
    empty_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    llm = _build_chat_llm()
    if llm is None:
        return None, empty_usage, {"messages": [], "tool_calls": [], "error": "LLM 未配置"}

    guide_context = "\n\n".join(rag_contexts) if rag_contexts else "暂无本地攻略上下文。"

    system_prompt = (
        "你是一名旅行规划助手。"
        "请用中文生成简洁的结构化旅行草稿。"
        "需要遵守用户给出的目的地、预算、节奏和本地攻略上下文。"
        "你必须只输出一个 JSON 对象，不要输出 Markdown，不要输出解释文字，不要输出代码块。"
        "输出内容必须严格符合给定的结构化字段要求。"
        "如果用户在额外备注里提出了明确诉求，例如看日落、不想早起、少辣、拍照等，你要优先把这些诉求落实到具体某一天的主要景点或当天安排里，而不是只写成泛泛的提示。"
        "如果用户明确提到想看日落，请优先把适合看日落的地点安排为某一天的主要景点，或至少让当天主景点与日落安排保持强关联。"
        "每次都必须先调用 web_search 搜索全网公开网页，无论本地知识库是否有资料；搜索结果返回后，如有相关页面，再调用 web_fetch 读取正文。优先知乎、携程、小红书、美团，同时接受其它可信来源。只依据检索到的资料陈述事实，不要编造来源。"
    )
    if not TAVILY_API_KEY:
        system_prompt += "当前未配置在线搜索服务；不得声称已进行网页搜索或引用未实际检索的网页。"

    human_prompt = f"""
目的地：{request.destination}
出发日期：{request.start_date.isoformat()}
结束日期：{request.end_date.isoformat()}
天数：{day_count}
人数：{request.travelers}
预算：{request.budget}
偏好：{'、'.join(request.preferences) if request.preferences else '无特别偏好'}
节奏：{request.pace or '适中'}
饮食偏好：{'、'.join(request.dietary_preferences) if request.dietary_preferences else '无'}
酒店档次：{request.hotel_level or '舒适型'}
额外备注：{request.special_notes or '无'}

本地攻略上下文：
{guide_context}

必须先调用 web_search；如结果中有相关公开页面，再调用 web_fetch 提取正文后规划。资料不足时不要虚构。

要求：
1. 输出一个整体 summary。
2. 输出 {day_count} 天的 daily draft。
3. 每天只给一个主要景点、一个餐饮建议和一条当天备注。
4. tips 保持简洁。
5. day_index 必须从 1 到 {day_count}。
6. 如果额外备注里有“想看日落”“不想早起”这类明确要求，必须在 days 中体现，不要只放到 tips。
7. 如果安排了看日落，当天的 spot_name 应尽量就是适合看日落的地点，或与 daily_note 中的日落安排保持一致，避免“主景点”和“日落地点”完全割裂。
8. 每天的安排要符合“轻松”节奏，避免过满、避免太早出发。
9. 餐饮建议尽量优先使用本地攻略上下文里已经出现的特色餐饮。
10. 只返回 JSON 对象，不要返回任何额外说明，不要使用 ```json 代码块。

JSON 结构示例：
{{
  "summary": "整体概述",
  "tips": ["提示1", "提示2"],
  "days": [
    {{
      "day_index": 1,
      "theme": "当天主题",
      "spot_name": "主要景点",
      "spot_description": "景点推荐理由",
      "meal_name": "餐饮名称",
      "meal_notes": "餐饮说明",
      "daily_note": "当天备注"
    }}
  ]
}}
"""

    print("[trip_planner_agent] 准备调用大模型...")
    print(f"[trip_planner_agent] model = {LLM_MODEL}")
    print(f"[trip_planner_agent] base_url = {LLM_BASE_URL or '<DEFAULT>'}")
    print(f"[trip_planner_agent] timeout = {LLM_TIMEOUT_SECONDS}s")
    print(f"[trip_planner_agent] max_retries = {LLM_MAX_RETRIES}")

    try:
        response, token_usage, research_sources, agent_trace = _invoke_with_web_tools(
            llm,
            [
                ("system", system_prompt),
                ("human", human_prompt),
            ],
            f"{request.destination} {'、'.join(request.preferences[:4])} {request.special_notes or ''}",
        )
    except Exception as exc:
        print(f"[trip_planner_agent] 大模型调用失败: {type(exc).__name__}: {exc}")
        return None, empty_usage, {"messages": [], "tool_calls": [], "error": f"{type(exc).__name__}: {exc}"}

    print(f"[trip_planner_agent] 大模型调用完成。token: prompt={token_usage['prompt_tokens']}, completion={token_usage['completion_tokens']}")

    raw_text = getattr(response, "content", "")
    if isinstance(raw_text, list):
        raw_text = "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in raw_text
        )

    json_text = _extract_json_object(str(raw_text))
    if json_text is None:
        print("[trip_planner_agent] 未能从模型返回中提取 JSON。")
        print(f"[trip_planner_agent] 原始返回预览: {str(raw_text)[:300]}")
        return None, token_usage, agent_trace

    try:
        result = PlannerDraft.model_validate(json.loads(json_text))
    except Exception as exc:
        print(f"[trip_planner_agent] JSON 解析失败: {type(exc).__name__}: {exc}")
        print(f"[trip_planner_agent] 原始返回预览: {str(raw_text)[:300]}")
        return None, token_usage, agent_trace

    if len(result.days) != day_count:
        print(
            "[trip_planner_agent] 结构化结果天数不匹配，"
            f"expected={day_count}, actual={len(result.days)}"
        )
        return None, token_usage, agent_trace

    agent_trace["task"] = "generate"
    result = result.model_copy(update={"research_sources": research_sources, "agent_trace": agent_trace})
    return result, token_usage, agent_trace


def generate_day_edit_draft(
    request: TripEditRequest,
    target_day: DayPlan,
) -> tuple[DayEditDraft | None, dict[str, int], dict]:
    """
    使用 LLM 生成单日编辑草稿。返回 (draft, token_usage)。

    这个函数只负责产出目标那一天的编辑结果，
    最终如何合并回完整 itinerary 由 service 层处理。
    """
    empty_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    llm = _build_chat_llm()
    if llm is None:
        return None, empty_usage, {"messages": [], "tool_calls": [], "error": "LLM 未配置"}

    current_day_payload = {
        "day_index": target_day.day_index,
        "date": target_day.date.isoformat() if target_day.date else None,
        "theme": target_day.theme,
        "spots": [spot.model_dump(mode="json") for spot in target_day.spots],
        "meals": [meal.model_dump(mode="json") for meal in target_day.meals],
        "notes": list(target_day.notes),
    }

    current_itinerary_payload = request.current_itinerary.model_dump(mode="json")

    system_prompt = (
        "你是一名旅行行程编辑助手。"
        "先评估用户编辑指令是否合理、可执行，是否违反日期、预算、地点或安全等约束。合理时 accepted=true 并只重写目标那一天；不合理时 accepted=false，说明原因并给出可行替代建议，原行程必须保留。"
        "你必须只输出一个 JSON 对象，不要输出 Markdown，不要输出解释文字，不要输出代码块。"
        "编辑结果要尽量保留原 itinerary 的整体风格、预算结构和轻松程度。"
        "每次修改行程都必须先调用 web_search 搜索全网公开网页；如命中相关页面，再调用 web_fetch 提取正文。优先知乎、携程、小红书、美团，同时接受其它可信来源。"
    )
    if not TAVILY_API_KEY:
        system_prompt += "当前未配置在线搜索服务；不得声称已进行网页搜索或引用未实际检索的网页。"

    human_prompt = f"""
当前完整 itinerary：
{json.dumps(current_itinerary_payload, ensure_ascii=False, indent=2)}

需要重点编辑的目标 day：
{json.dumps(current_day_payload, ensure_ascii=False, indent=2)}

用户编辑指令：{request.user_instruction}
编辑范围：{request.edit_scope or '未指定'}
需要尽量保留的约束：{', '.join(request.preserve_constraints) if request.preserve_constraints else '无'}

先调用 web_search 搜索公开页面；如有相关结果，再调用 web_fetch 提取正文后修改行程。来源不足时不要编造事实。

要求：
1. 先判断要求是否合理且可执行；若不合理，accepted=false，填写 decision_reason 和 alternative_suggestion，不得改变目标日行程字段。
2. 合理时 accepted=true，填写 decision_reason，并只编辑目标那一天。
3. 如果用户要求“更轻松”“不要安排太满”，请减少固定景点压力，让备注更自然。
4. 尽量延续原 itinerary 的城市、风格、餐饮语气和预算结构。
5. 只返回 JSON 对象。

JSON 结构示例：
{{
  "theme": "编辑后的当天主题",
  "spot_name": "编辑后的主要景点",
  "spot_description": "编辑后的景点说明",
  "meal_name": "编辑后的餐饮名称",
  "meal_notes": "编辑后的餐饮说明",
  "daily_note": "编辑后的当天备注",
  "accepted": true,
  "decision_reason": "符合行程约束，可以执行",
  "alternative_suggestion": ""
}}
"""

    print("[trip_planner_agent] 准备调用大模型进行单日编辑...")
    print(f"[trip_planner_agent] model = {LLM_MODEL}")
    print(f"[trip_planner_agent] base_url = {LLM_BASE_URL or '<DEFAULT>'}")

    try:
        response, token_usage, research_sources, agent_trace = _invoke_with_web_tools(
            llm,
            [
                ("system", system_prompt),
                ("human", human_prompt),
            ],
            f"{request.current_itinerary.destination} {request.user_instruction}",
        )
    except Exception as exc:
        print(f"[trip_planner_agent] 单日编辑调用失败: {type(exc).__name__}: {exc}")
        return None, empty_usage, {"messages": [], "tool_calls": [], "error": f"{type(exc).__name__}: {exc}"}

    print(f"[trip_planner_agent] 单日编辑调用完成。token: prompt={token_usage['prompt_tokens']}, completion={token_usage['completion_tokens']}")

    raw_text = getattr(response, "content", "")
    if isinstance(raw_text, list):
        raw_text = "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in raw_text
        )

    json_text = _extract_json_object(str(raw_text))
    if json_text is None:
        print("[trip_planner_agent] 未能从单日编辑结果中提取 JSON。")
        print(f"[trip_planner_agent] 原始返回预览: {str(raw_text)[:300]}")
        return None, token_usage, agent_trace

    try:
        payload = json.loads(json_text)
        if not isinstance(payload, dict):
            raise ValueError("单日编辑结果不是 JSON 对象。")
        normalized_payload = _normalize_day_edit_payload(payload)
        draft = DayEditDraft.model_validate(normalized_payload)
        agent_trace["task"] = "edit"
        draft = draft.model_copy(update={"research_sources": research_sources, "agent_trace": agent_trace})
        return draft, token_usage, agent_trace
    except Exception as exc:
        print(f"[trip_planner_agent] 单日编辑 JSON 解析失败: {type(exc).__name__}: {exc}")
        print(f"[trip_planner_agent] 原始返回预览: {str(raw_text)[:300]}")
        return None, token_usage, agent_trace
