"""
LLM辅助匹配模块 - 使用大语言模型匹配未识别的清单项

支持分层匹配：先用低成本 flash 模型快速匹配，
低置信度结果自动用 plus 模型复核，平衡速度与准确率。
"""

import json
import re
from openai import OpenAI


# 各模型默认配置
MODEL_PRESETS = {
    "qwen-flash": {
        "model": "qwen3.5-flash",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "qwen-plus": {
        "model": "qwen3.5-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "qwen-vl": {
        "model": "qwen3-vl-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "openai-gpt4o-mini": {
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
    },
    "openai-gpt4o": {
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
    },
    "deepseek": {
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com/v1",
    },
    "kimi": {
        "model": "kimi-k2.5",
        "base_url": "https://api.moonshot.cn/v1",
    },
    "minimax": {
        "model": "MiniMax-M2.5",
        "base_url": "https://api.minimax.chat/v1",
    },
    "zhipu-glm4": {
        "model": "glm-4",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
    "qwen": {
        "model": "qwen-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "ollama": {
        "model": "qwen2.5:7b",
        "base_url": "http://localhost:11434/v1",
    },
}

# 分层匹配默认阈值：低于此置信度的结果将进入复核
DEFAULT_RECHECK_THRESHOLD = 0.5


def _build_prompt(unmatched_items, scanned_names):
    """构建LLM匹配的提示词"""
    items_text = json.dumps(
        [{"index": item["index"], "name": item["name"]} for item in unmatched_items],
        ensure_ascii=False,
    )
    files_text = json.dumps(scanned_names, ensure_ascii=False)

    return f"""你是一个审计文件匹配助手。下面是客户资料文件夹中扫描到的文件名列表，以及需要核对的清单项。

可用文件列表：
{files_text}

需要匹配的清单项：
{items_text}

请判断每个清单项最可能对应哪个文件。匹配规则：
1. 忽略序号前缀（如 "1."、"(2)"）和括号内的说明文字
2. 中英文可以互相匹配（如 "银行存款明细表" 可以匹配 "Bank Balance Check"）
3. 日期可以忽略，重点匹配业务含义
4. 如果清单项包含多个关键词，文件名需要体现主要业务含义即可，不要求完全一致
5. 如果确定没有合适的文件，matched_name 设为 null

返回 JSON 数组格式（仅返回 JSON，不要其他内容）：
[{{"index": 1, "matched_name": "文件名或null", "confidence": 0.85}}]

其中 confidence 是你对匹配结果的置信度（0-1），低于 0.5 的匹配视为无效。"""


def _build_recheck_prompt(unmatched_items, scanned_names, initial_results):
    """构建复核提示词，带上第一轮结果供参考"""
    items_text = json.dumps(
        [{"index": item["index"], "name": item["name"]} for item in unmatched_items],
        ensure_ascii=False,
    )
    files_text = json.dumps(scanned_names, ensure_ascii=False)
    prev_text = json.dumps(initial_results, ensure_ascii=False, indent=2)

    return f"""你是一个审计文件匹配助手。这些条目在第一轮匹配中置信度较低，请谨慎复核。

可用文件列表：
{files_text}

需要复核的清单项：
{items_text}

第一轮匹配结果（供参考）：
{prev_text}

请重新判断每个清单项最可能对应哪个文件。匹配规则：
1. 忽略序号前缀和括号内说明文字
2. 中英文可以互相匹配
3. 日期可忽略，重点匹配业务含义
4. 确认有合理依据才给高置信度，无依据则 matched_name 设为 null

返回 JSON 数组格式（仅返回 JSON）：
[{{"index": 1, "matched_name": "文件名或null", "confidence": 0.85}}]

confidence 低于 0.5 的匹配视为无效。"""


def _parse_llm_response(response_text):
    """解析LLM返回的JSON结果"""
    # 尝试提取JSON部分（LLM可能返回多余文字）
    json_match = re.search(r'\[[\s\S]*\]', response_text)
    if json_match:
        return json.loads(json_match.group())
    raise ValueError("无法从LLM响应中提取JSON结果")


def _call_llm(unmatched_items, scanned_names, provider, api_key, model, base_url):
    """调用指定 LLM 执行匹配，返回 (results, usage)"""
    client = OpenAI(api_key=api_key or "no-key", base_url=base_url)
    prompt = _build_prompt(unmatched_items, scanned_names)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是审计文件匹配助手，请严格按照要求返回JSON格式结果。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )

    response_text = response.choices[0].message.content
    results = _parse_llm_response(response_text)

    usage = {}
    if response.usage:
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }

    return results, usage


def llm_match(unmatched_items, scanned_names, config):
    """
    使用LLM匹配未识别的清单项（支持分层匹配）

    参数:
      unmatched_items: [{"index": 1, "name": "银行存款明细表..."}]
      scanned_names: ["银行对账单.xlsx", "存款明细.pdf", ...]
      config: {
        "provider": "qwen-flash",          # 默认主模型
        "api_key": "sk-xxx",
        "model": "",                        # 可选，覆盖provider默认model
        "base_url": "",                     # 可选，覆盖provider默认base_url
        "recheck_threshold": 0.5,           # 低于此置信度触发复核
      }

    返回:
      {
        "results": [{"index": 1, "matched_name": "文件名", "confidence": 0.85, "source": "flash"}],
        "usage": { "prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ... },
        "stages": { "flash": {...}, "recheck": {...} }
      }
    """
    api_key = config.get("api_key", "")
    # --- 第一轮：flash 快速匹配 ---
    provider = config.get("provider", "qwen-flash")
    preset = MODEL_PRESETS.get(provider, MODEL_PRESETS["qwen-flash"])
    model = config.get("model") or preset["model"]
    base_url = config.get("base_url") or preset["base_url"]

    if not api_key:
        raise ValueError("请提供API Key")

    flash_results, flash_usage = _call_llm(
        unmatched_items, scanned_names, provider, api_key, model, base_url
    )

    # 标记来源
    for r in flash_results:
        r["source"] = "flash"

    # 汇总用量
    total_usage = dict(flash_usage) if flash_usage else {}
    stages = {"flash": {"usage": flash_usage, "results": list(flash_results)}}

    # --- 第二轮：低置信度条目用 plus 复核 ---
    threshold = config.get("recheck_threshold", DEFAULT_RECHECK_THRESHOLD)
    low_conf_items = [
        item for item, result in zip(unmatched_items, flash_results)
        if result.get("confidence", 0) < threshold
    ]

    if low_conf_items:
        # 复核必须沿用第一轮的供应商配置，避免 API Key 被发送到其他供应商。
        recheck_provider = provider
        recheck_model = model
        recheck_base_url = base_url

        # 用复核模型再做一次匹配（只传低置信度的条目）
        recheck_results, recheck_usage = _call_llm(
            low_conf_items, scanned_names, recheck_provider,
            api_key, recheck_model, recheck_base_url,
        )

        for r in recheck_results:
            r["source"] = "recheck"

        # 合并：用复核结果覆盖 flash 中低置信度的条目
        recheck_by_index = {r["index"]: r for r in recheck_results}
        for r in flash_results:
            if r["index"] in recheck_by_index:
                repl = recheck_by_index[r["index"]]
                r["matched_name"] = repl["matched_name"]
                r["confidence"] = repl["confidence"]
                r["source"] = repl["source"]

        # 合并用量
        stages["recheck"] = {"usage": recheck_usage, "results": recheck_results}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if recheck_usage:
                total_usage[key] = total_usage.get(key, 0) + recheck_usage.get(key, 0)

    return {
        "results": flash_results,
        "usage": total_usage,
        "stages": stages,
    }
