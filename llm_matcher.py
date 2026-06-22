"""LLM辅助匹配模块 - 使用大语言模型匹配未识别的清单项"""

import json
import re
from openai import OpenAI


# 各模型默认配置
MODEL_PRESETS = {
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
5. 如果确实没有合适的文件，matched_name 设为 null

返回 JSON 数组格式（仅返回 JSON，不要其他内容）：
[{{"index": 1, "matched_name": "文件名或null", "confidence": 0.85}}]

其中 confidence 是你对匹配结果的置信度（0-1），低于 0.5 的匹配视为无效。"""


def _parse_llm_response(response_text):
    """解析LLM返回的JSON结果"""
    # 尝试提取JSON部分（LLM可能返回多余文字）
    json_match = re.search(r'\[[\s\S]*\]', response_text)
    if json_match:
        return json.loads(json_match.group())
    raise ValueError("无法从LLM响应中提取JSON结果")


def llm_match(unmatched_items, scanned_names, config):
    """
    使用LLM匹配未识别的清单项

    参数:
      unmatched_items: [{"index": 1, "name": "银行存款明细表..."}]
      scanned_names: ["银行对账单.xlsx", "存款明细.pdf", ...]
      config: {"provider": "deepseek", "api_key": "sk-xxx"}

    返回:
      {"results": [{"index": 1, "matched_name": "文件名", "confidence": 0.85}], "usage": {...}}
    """
    provider = config.get("provider", "deepseek")
    api_key = config.get("api_key", "")
    custom_model = config.get("model", "")
    custom_base_url = config.get("base_url", "")

    if not api_key and provider != "ollama":
        raise ValueError("请提供API Key")

    preset = MODEL_PRESETS.get(provider, MODEL_PRESETS["deepseek"])
    model = custom_model or preset["model"]
    base_url = custom_base_url or preset["base_url"]

    client = OpenAI(api_key=api_key or "ollama", base_url=base_url)

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

    return {"results": results, "usage": usage}
