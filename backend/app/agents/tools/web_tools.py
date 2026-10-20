from __future__ import annotations

import json
import ipaddress
from urllib.parse import urlsplit

import httpx

from app.config import TAVILY_API_KEY, TAVILY_BASE_URL, TAVILY_TIMEOUT_SECONDS


MAX_SEARCH_RESULTS = 5
MAX_CONTENT_CHARS = 5000
PREFERRED_SOURCES = "知乎 携程 小红书 美团"
PREFERRED_DOMAINS = ["zhihu.com", "ctrip.com", "xiaohongshu.com", "meituan.com", "dianping.com"]

WEB_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the public web for travel information. Search every time, regardless of local knowledge-base coverage. "
                "Prefer useful public pages from Zhihu, Ctrip, Xiaohongshu, and Meituan, while accepting other reputable sources."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Specific travel research query"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Extract readable content from one publicly accessible page returned by web_search. Do not fetch login-only or restricted pages.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Public page URL returned by web_search"}},
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
]


def _allowed_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return False
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global:
                return False
        except ValueError:
            pass
        return True
    except ValueError:
        return False


def _post(endpoint: str, payload: dict) -> dict:
    if not TAVILY_API_KEY:
        raise RuntimeError("在线搜索未配置 TAVILY_API_KEY。")
    response = httpx.post(
        f"{TAVILY_BASE_URL.rstrip('/')}/{endpoint}",
        json={"api_key": TAVILY_API_KEY, **payload},
        timeout=TAVILY_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def web_search(query: str) -> tuple[str, list[dict[str, str]]]:
    query = query.strip()
    if not query:
        raise ValueError("query 不能为空。")

    search_query = f"{query} 旅游攻略 景点 美食 交通"
    general = _post(
        "search",
        {
            "query": search_query,
            "max_results": MAX_SEARCH_RESULTS,
            "search_depth": "advanced",
            "include_answer": False,
        },
    )
    preferred = _post(
        "search",
        {
            "query": f"{search_query} {PREFERRED_SOURCES}",
            "include_domains": PREFERRED_DOMAINS,
            "max_results": MAX_SEARCH_RESULTS,
            "search_depth": "advanced",
            "include_answer": False,
        },
    )

    general_results: dict[str, dict[str, str]] = {}
    preferred_results: dict[str, dict[str, str]] = {}
    _collect_search_results(general, general_results)
    _collect_search_results(preferred, preferred_results)
    # 保留通用搜索的高相关候选，再加入偏好平台的结果，避免域名定向搜索带来噪声。
    results_by_url = dict(list(general_results.items())[:3])
    for url, item in preferred_results.items():
        if len(results_by_url) >= MAX_SEARCH_RESULTS:
            break
        results_by_url.setdefault(url, item)

    results = list(results_by_url.values())[:MAX_SEARCH_RESULTS]
    if not results:
        return "没有找到符合条件的公开网页结果。", []
    sources = [
        {"title": item["title"], "url": item["url"], "published_date": item["published_date"]}
        for item in results
    ]
    return json.dumps({"results": results}, ensure_ascii=False), sources


def _collect_search_results(data: dict, results_by_url: dict[str, dict[str, str]]) -> None:
    for item in data.get("results", []):
        url = str(item.get("url", ""))
        if not _allowed_url(url) or url in results_by_url:
            continue
        results_by_url[url] = {
            "title": str(item.get("title") or "未命名网页")[:300],
            "url": url,
            "published_date": str(item.get("published_date") or ""),
            "snippet": str(item.get("content") or "")[:1200],
        }

def web_fetch_many(urls: list[str], allowed_urls: set[str] | None = None) -> tuple[str, list[dict[str, str]]]:
    checked_urls = []
    for url in urls[:3]:
        url = url.strip()
        if not _allowed_url(url):
            continue
        if allowed_urls is not None and url not in allowed_urls:
            continue
        if url not in checked_urls:
            checked_urls.append(url)
    if not checked_urls:
        return json.dumps({"results": [], "error": "没有可提取的本次搜索公开页面"}, ensure_ascii=False), []

    data = _post("extract", {"urls": checked_urls, "extract_depth": "basic", "include_images": False})
    fetched = []
    sources = []
    for item in data.get("results", []):
        url = str(item.get("url", ""))
        content = str(item.get("raw_content") or "").strip()
        if not content:
            continue
        fetched.append({"url": url, "content": content[:MAX_CONTENT_CHARS]})
        sources.append({"title": "网页正文", "url": url, "published_date": ""})
    for item in data.get("failed_results", []):
        fetched.append({"url": str(item.get("url", "")), "error": str(item.get("error", "页面无法提取正文"))[:300]})
    if not fetched:
        fetched = [{"url": url, "error": "搜索服务没有返回可读正文"} for url in checked_urls]
    return json.dumps({"results": fetched}, ensure_ascii=False), sources


def web_fetch(url: str, allowed_urls: set[str] | None = None) -> tuple[str, list[dict[str, str]]]:
    return web_fetch_many([url], allowed_urls=allowed_urls)

def execute_web_tool(name: str, arguments: object, allowed_urls: set[str] | None = None) -> tuple[str, list[dict[str, str]]]:
    if not isinstance(arguments, dict):
        raise ValueError("工具 arguments 必须是 JSON 对象。")
    if name == "web_search":
        return web_search(str(arguments.get("query", "")))
    if name == "web_fetch":
        return web_fetch(str(arguments.get("url", "")), allowed_urls=allowed_urls)
    raise ValueError(f"不允许调用工具：{name}")

