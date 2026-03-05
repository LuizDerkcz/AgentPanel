from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.config import get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test OpenRouter connectivity, proxy routing, and model availability."
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=[
            "google/gemini-2.5-flash",
            "anthropic/claude-3.5-sonnet",
        ],
        help="OpenRouter model IDs to test.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=25.0,
        help="HTTP timeout in seconds for each request.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=32,
        help="Max tokens for the chat completion test call.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override OpenRouter base URL (default from settings/.env).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Override OPENAI_API_KEY from CLI.",
    )
    parser.add_argument(
        "--skip-model-call",
        action="store_true",
        help="Only test network/proxy diagnostics without model completion calls.",
    )
    return parser.parse_args()


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    latency_ms: int | None = None


@dataclass
class ModelResult:
    model: str
    ok: bool
    detail: str
    latency_ms: int | None = None


def _mask_key(key: str) -> str:
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def _parse_host(url: str) -> str:
    clean = url.replace("https://", "").replace("http://", "")
    return clean.split("/")[0].split(":")[0]


def _proxy_env_value() -> str:
    for env_name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.getenv(env_name)
        if value:
            return f"{env_name}={value}"
    return "未检测到 HTTP(S)_PROXY/ALL_PROXY"


async def check_dns(target_host: str) -> CheckResult:
    start = time.perf_counter()
    try:
        loop = asyncio.get_running_loop()
        await loop.getaddrinfo(target_host, 443, type=socket.SOCK_STREAM)
        elapsed = int((time.perf_counter() - start) * 1000)
        return CheckResult("DNS 解析", True, f"{target_host} 可解析", elapsed)
    except Exception as exc:
        return CheckResult("DNS 解析", False, f"{target_host} 解析失败: {exc}")


async def check_http_head(base_url: str, timeout: float) -> CheckResult:
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=True, follow_redirects=True) as client:
            response = await client.get(f"{base_url.rstrip('/')}/models")
        elapsed = int((time.perf_counter() - start) * 1000)
        if response.status_code in {401, 403}:
            return CheckResult("HTTP 连通", True, f"返回 {response.status_code}（未授权但网络可达）", elapsed)
        if 200 <= response.status_code < 500:
            return CheckResult("HTTP 连通", True, f"返回 {response.status_code}（网络可达）", elapsed)
        return CheckResult("HTTP 连通", False, f"返回异常状态码 {response.status_code}", elapsed)
    except Exception as exc:
        return CheckResult("HTTP 连通", False, f"请求失败: {exc}")


async def check_model(
    *,
    client: AsyncOpenAI,
    model: str,
    max_tokens: int,
) -> ModelResult:
    start = time.perf_counter()
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a concise assistant."},
                {"role": "user", "content": "Reply exactly with: pong"},
            ],
            temperature=0,
            max_tokens=max_tokens,
        )
        elapsed = int((time.perf_counter() - start) * 1000)
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            return ModelResult(model, False, "响应为空", elapsed)
        preview = content.replace("\n", " ")[:120]
        return ModelResult(model, True, f"调用成功，响应预览: {preview}", elapsed)
    except Exception as exc:
        elapsed = int((time.perf_counter() - start) * 1000)
        return ModelResult(model, False, f"调用失败: {exc}", elapsed)


def print_header(title: str) -> None:
    print(f"\n=== {title} ===")


def print_check(result: CheckResult) -> None:
    mark = "✅" if result.ok else "❌"
    latency = f" | {result.latency_ms} ms" if result.latency_ms is not None else ""
    print(f"{mark} {result.name}: {result.detail}{latency}")


def print_model(result: ModelResult) -> None:
    mark = "✅" if result.ok else "❌"
    latency = f" | {result.latency_ms} ms" if result.latency_ms is not None else ""
    print(f"{mark} {result.model}: {result.detail}{latency}")


async def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    settings = get_settings()
    args = parse_args()

    base_url = (args.base_url or settings.openai_api_base).rstrip("/")
    api_key = args.api_key or settings.openai_api_key or os.getenv("OPENAI_API_KEY")

    print_header("OpenRouter 测试配置")
    print(f"Base URL: {base_url}")
    print(f"API Key: {_mask_key(api_key) if api_key else '未配置'}")
    print(f"代理环境: {_proxy_env_value()}")
    print(f"测试模型: {', '.join(args.models)}")

    diagnostics: list[CheckResult] = []
    diagnostics.append(await check_dns(_parse_host(base_url)))
    diagnostics.append(await check_http_head(base_url, args.timeout))

    print_header("网络与代理诊断")
    for item in diagnostics:
        print_check(item)

    model_results: list[ModelResult] = []
    if args.skip_model_call:
        print_header("模型可用性")
        print("⏭️ 已跳过模型调用（--skip-model-call）")
    elif not api_key:
        print_header("模型可用性")
        print("❌ OPENAI_API_KEY 未配置，无法进行模型调用")
        return 2
    else:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=args.timeout,
            max_retries=0,
        )
        model_results = await asyncio.gather(
            *[
                check_model(client=client, model=model, max_tokens=args.max_tokens)
                for model in args.models
            ]
        )

        print_header("模型可用性")
        for result in model_results:
            print_model(result)

    diag_ok = all(item.ok for item in diagnostics)
    model_ok = all(item.ok for item in model_results) if model_results else args.skip_model_call
    overall_ok = diag_ok and model_ok

    print_header("结论")
    if overall_ok:
        print("✅ OpenRouter 网络与模型调用均可用")
        return 0

    if not diag_ok:
        print("❌ 网络或代理链路存在问题，请先修复诊断项")
    elif not model_ok:
        print("❌ 网络可达，但至少一个模型调用失败（可能是模型ID、权限或额度问题）")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
