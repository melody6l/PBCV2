"""
LLM辅助匹配模块 - 使用大语言模型匹配未识别的清单项

支持分层匹配：先用低成本 flash 模型快速匹配，
低置信度结果自动用 plus 模型复核，平衡速度与准确率。
"""

import json
import re
import requests
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
    "dify-chat": {
        "model": "",
        "base_url": "",
    },
}

# 分层匹配默认阈值：低于此置信度的结果将进入复核
DEFAULT_RECHECK_THRESHOLD = 0.5

# 匹配规则（公共常量，_build_prompt 和 _build_recheck_prompt 共用）
MATCH_RULES = """匹配规则：
1. 忽略纯序号前缀（如 "1."、"(2)"、"一、"）和括号内纯序号
2. 括号内如果是关键区分信息（如公司代码 CN02/CN03、合并/单体/母公司），则必须匹配
3. 括号内如果只是英文翻译或补充说明，可以忽略
4. 中英文可以互相匹配
5. 日期可以忽略，重点匹配业务含义
6. 如果清单项包含多个关键词，文件名需要体现主要业务含义即可，不要求完全一致
7. 如果确定没有合适的文件，matched_name 设为 null"""


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

请判断每个清单项最可能对应哪个文件。{MATCH_RULES}

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

请重新判断每个清单项最可能对应哪个文件。{MATCH_RULES}

返回 JSON 数组格式（仅返回 JSON）：
[{{"index": 1, "matched_name": "文件名或null", "confidence": 0.85}}]

confidence 低于 0.5 的匹配视为无效。"""


# 内容匹配专用规则（在原有 MATCH_RULES 基础上追加）
CONTENT_MATCH_RULES = MATCH_RULES + """
8. OCR识别内容可能存在文字误差，请结合文件名和内容综合判断
9. Excel文件关注Sheet名称、列标题和具体数据内容
10. 文件内容仅显示前段摘要，未显示部分可能包含更多信息"""

# 各内容类型送入 LLM 的截断长度
CONTENT_TRUNCATION = {"ocr": 1000, "text": 2000, "markdown": 2000, "structured": 0}


def _dify_chat_messages_url(base_url):
    url = str(base_url or "").strip().rstrip("/")
    return url if url.endswith("/chat-messages") else f"{url}/chat-messages"


def _chat_completion_text(system_prompt, user_prompt, provider, api_key, model, base_url, config=None):
    """调用 OpenAI 兼容接口或 Dify Chat App，返回正文及用量。"""
    if provider == "dify-chat":
        if not base_url or not api_key:
            raise ValueError("Dify Chat App 请填写 Base URL 和 API Key")
        timeout = max(10, min(300, int((config or {}).get("timeout", 120))))
        query = (
            f"system:\n{system_prompt}\n\nuser:\n{user_prompt}"
            "\n\n请只返回严格 JSON，不要 Markdown、不要解释、不要代码块。"
        )
        response = requests.post(
            _dify_chat_messages_url(base_url),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "inputs": {},
                "query": query,
                "response_mode": "blocking",
                "user": "pbc-file-matcher",
            },
            timeout=timeout,
        )
        if not response.ok:
            detail = response.text[:500]
            raise ValueError(f"Dify HTTP {response.status_code}: {detail}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("Dify 返回的不是有效 JSON") from exc
        answer = payload.get("answer") if isinstance(payload, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Dify 服务已响应，但未返回 answer 正文")
        metadata = payload.get("metadata") if isinstance(payload, dict) else {}
        usage_data = metadata.get("usage") if isinstance(metadata, dict) else {}
        usage = {}
        if isinstance(usage_data, dict):
            usage = {
                "prompt_tokens": usage_data.get("prompt_tokens", 0),
                "completion_tokens": usage_data.get("completion_tokens", 0),
                "total_tokens": usage_data.get("total_tokens", 0),
            }
        return answer.strip(), usage

    client = OpenAI(api_key=api_key or "no-key", base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
    )
    text = response.choices[0].message.content
    usage = {}
    if response.usage:
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
    return text, usage


def _build_content_prompt(unmatched_items, scanned_items):
    """构建含文件内容的 LLM 匹配提示词。scaned_items 含 name + content 字段。"""
    items_text = json.dumps(
        [{"index": item["index"], "name": item["name"]} for item in unmatched_items],
        ensure_ascii=False,
    )

    file_entries = []
    for si in scanned_items:
        name = si["name"]
        content = (si.get("content") or "").strip()
        ct = si.get("content_type") or ""
        label = si.get("content_label") or ""
        if content:
            limit = CONTENT_TRUNCATION.get(ct, 2000)
            preview = (content[:limit] + "…") if limit > 0 and len(content) > limit else content
            entry = f"- {name}\n  [内容类型: {label}]\n  [内容预览: {preview}]"
            if ct == "ocr":
                entry += "\n  [注意: OCR识别结果，可能存在文字误差]"
        else:
            entry = f"- {name}"
        file_entries.append(entry)
    files_text = "\n".join(file_entries)

    return f"""你是一个审计文件匹配助手。下面是客户资料文件夹中扫描到的文件列表（含文件内容摘要），以及需要核对的清单项。

可用文件列表（共{len(scanned_items)}个）：
{files_text}

需要匹配的清单项：
{items_text}

请根据文件名和文件内容，判断每个清单项最可能对应哪个文件。{CONTENT_MATCH_RULES}

返回 JSON 数组格式（仅返回 JSON，不要其他内容）：
[{{"index": 1, "matched_name": "文件名或null", "confidence": 0.85}}]

其中 confidence 是你对匹配结果的置信度（0-1），低于 0.5 的匹配视为无效。"""


def _build_content_recheck_prompt(unmatched_items, scanned_items, initial_results):
    """构建含文件内容的复核提示词。"""
    items_text = json.dumps(
        [{"index": item["index"], "name": item["name"]} for item in unmatched_items],
        ensure_ascii=False,
    )

    file_entries = []
    for si in scanned_items:
        name = si["name"]
        content = (si.get("content") or "").strip()
        ct = si.get("content_type") or ""
        label = si.get("content_label") or ""
        if content:
            limit = CONTENT_TRUNCATION.get(ct, 2000)
            preview = (content[:limit] + "…") if limit > 0 and len(content) > limit else content
            entry = f"- {name}\n  [内容类型: {label}]\n  [内容预览: {preview}]"
            if ct == "ocr":
                entry += "\n  [OCR可能有误差]"
        else:
            entry = f"- {name}"
        file_entries.append(entry)
    files_text = "\n".join(file_entries)
    prev_text = json.dumps(initial_results, ensure_ascii=False, indent=2)

    return f"""你是一个审计文件匹配助手。这些条目在第一轮匹配中置信度较低，请谨慎复核。

可用文件列表（含内容）：
{files_text}

需要复核的清单项：
{items_text}

第一轮匹配结果（供参考）：
{prev_text}

请重新判断每个清单项最可能对应哪个文件。{CONTENT_MATCH_RULES}

返回 JSON 数组格式（仅返回 JSON）：
[{{"index": 1, "matched_name": "文件名或null", "confidence": 0.85}}]

confidence 低于 0.5 的匹配视为无效。"""


def _call_llm_with_content(unmatched_items, scanned_items, provider, api_key, model, base_url):
    """调用 LLM 执行内容匹配（含文件内容）。"""
    prompt = _build_content_prompt(unmatched_items, scanned_items)
    response_text, usage = _chat_completion_text(
        "你是审计文件匹配助手，请根据文件内容和名称判断匹配关系，严格返回JSON格式结果。",
        prompt, provider, api_key, model, base_url,
    )
    results = _parse_llm_response(response_text)
    return results, usage


def _call_llm_with_content_recheck(unmatched_items, scanned_items, provider, api_key, model, base_url, initial_results):
    """调用 LLM 执行内容复核。"""
    prompt = _build_content_recheck_prompt(unmatched_items, scanned_items, initial_results)
    response_text, usage = _chat_completion_text(
        "你是审计文件匹配助手，这些条目置信度较低请谨慎复核，严格返回JSON格式结果。",
        prompt, provider, api_key, model, base_url,
    )
    results = _parse_llm_response(response_text)
    return results, usage


def llm_match_with_content(unmatched_items, scanned_items, config):
    """
    使用 LLM 匹配未识别的清单项（含文件内容，支持分层匹配）。

    Args:
        unmatched_items: [{"index": 1, "name": "银行存款明细表..."}]
        scanned_items: [{"name": "银行对账单.xlsx", "content": "...", "content_type": "structured", ...}]
        config: {"provider": "qwen-flash", "api_key": "sk-xxx", "model": "", "base_url": "", "recheck_threshold": 0.5}

    Returns:
        {"results": [...], "usage": {...}, "stages": {...}}
    """
    api_key = config.get("api_key", "")
    provider = config.get("provider", "qwen-flash")
    preset = MODEL_PRESETS.get(provider, MODEL_PRESETS["qwen-flash"])
    model = config.get("model") or preset["model"]
    base_url = config.get("base_url") or preset["base_url"]

    if not api_key:
        raise ValueError("请提供API Key")

    # --- 第一轮：flash 快速匹配 ---
    flash_results, flash_usage = _call_llm_with_content(
        unmatched_items, scanned_items, provider, api_key, model, base_url
    )

    for r in flash_results:
        r["source"] = "flash"

    total_usage = dict(flash_usage) if flash_usage else {}
    stages = {"flash": {"usage": flash_usage, "results": list(flash_results)}}

    # --- 第二轮：低置信度复核 ---
    threshold = config.get("recheck_threshold", DEFAULT_RECHECK_THRESHOLD)
    low_conf_items = [
        item for item, result in zip(unmatched_items, flash_results)
        if result.get("confidence", 0) < threshold
    ]

    if low_conf_items:
        FLASH_TO_PLUS = {
            "qwen-flash": "qwen-plus",
            "openai-gpt4o-mini": "openai-gpt4o",
        }
        if provider in FLASH_TO_PLUS and config.get("recheck_provider") is None:
            recheck_provider = FLASH_TO_PLUS[provider]
            recheck_preset = MODEL_PRESETS[recheck_provider]
            recheck_model = recheck_preset["model"]
            recheck_base_url = recheck_preset["base_url"]
        else:
            recheck_provider = provider
            recheck_model = model
            recheck_base_url = base_url

        recheck_results, recheck_usage = _call_llm_with_content_recheck(
            low_conf_items, scanned_items, recheck_provider,
            api_key, recheck_model, recheck_base_url,
            [r for item, r in zip(unmatched_items, flash_results) if r.get("confidence", 0) < threshold],
        )

        for r in recheck_results:
            r["source"] = "recheck"

        recheck_by_index = {r["index"]: r for r in recheck_results}
        for r in flash_results:
            if r["index"] in recheck_by_index:
                repl = recheck_by_index[r["index"]]
                r["matched_name"] = repl["matched_name"]
                r["confidence"] = repl["confidence"]
                r["source"] = repl["source"]

        stages["recheck"] = {"usage": recheck_usage, "results": recheck_results}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if recheck_usage:
                total_usage[key] = total_usage.get(key, 0) + recheck_usage.get(key, 0)

    return {
        "results": flash_results,
        "usage": total_usage,
        "stages": stages,
    }


def _build_file_classification_prompt(checklist_items, scanned_items, companies=None, initial_results=None):
    """构建“未归属文件 → 清单项 + 公司”的内容分类提示词。"""
    items_text = json.dumps(
        [{"index": item["index"], "name": item["name"]} for item in checklist_items],
        ensure_ascii=False,
    )
    files = []
    for item in scanned_items:
        content = (item.get("content") or "").strip()
        content_type = item.get("content_type") or ""
        limit = CONTENT_TRUNCATION.get(content_type, 2000)
        preview = (content[:limit] + "…") if limit > 0 and len(content) > limit else content
        files.append({
            "file_id": item["file_id"],
            "name": item["name"],
            "relative_path": item.get("relative_path", item["name"]),
            "fixed_checklist_index": item.get("fixed_checklist_index"),
            "content_type": item.get("content_label", content_type),
            "content_preview": preview,
        })
    previous = ""
    if initial_results is not None:
        previous = "\n上一轮分类结果（请重点复核低置信度项目）：\n" + json.dumps(
            initial_results, ensure_ascii=False, indent=2
        )
    company_candidates = [
        {
            "company_name": company.get("short_name") or company.get("full_name"),
            "full_name": company.get("full_name", ""),
            "short_name": company.get("short_name", ""),
        }
        for company in (companies or [])
        if company.get("short_name") or company.get("full_name")
    ]
    return f"""你是审计资料文件分类助手。请逐个判断每个未归属文件属于哪个清单项，以及资料实际属于哪家公司。

全部清单项：
{items_text}

公司封闭候选集（只能从 company_name 中选择，不得创造新公司）：
{json.dumps(company_candidates, ensure_ascii=False)}

未归属文件（含内容摘要）：
{json.dumps(files, ensure_ascii=False)}
{previous}
分类规则：
1. 必须为每个 file_id 返回且只返回一条结果。
2. 一个清单项可以接收多个文件；不要因为某清单项可能已有文件就排除它。
3. 综合文件名、相对路径和内容判断；OCR 内容可能有错字。
4. 若文件与任何清单项都无关，checklist_index 必须为 null，不要强行分类。
5. 若文件提供 fixed_checklist_index，说明清单项已经确认，checklist_index 必须原样返回；此时重点识别公司，不得改换清单项。
6. checklist_confidence 和 company_confidence 均为 0 到 1，分别表达两个维度的把握。
7. 公司判断应优先依据正文中的公司全称/简称、抬头、落款、盖章、统一社会信用代码等；仅出现“本公司”、合同相对方或页眉集团名时不得强行归属。
8. detected_company_name 返回正文中实际识别到的公司主体原文（可为英文）；无法提取时返回 null。
9. 无法确定公司、涉及多家公司或公共资料时，company_name 必须为 null；company_evidence 返回不超过3条简短证据，不得编造正文内容。
10. reason 用一句话说明清单项判断的最关键依据，不得包含大段原文。

仅返回 JSON 数组：
[{{"file_id":"f1","checklist_index":8,"checklist_confidence":0.87,"detected_company_name":"COMPANY A CO LTD","company_name":"公司A","company_confidence":0.91,"company_evidence":["账户抬头出现 COMPANY A CO LTD"],"reason":"包含银行账号及逐笔交易记录"}},
 {{"file_id":"f2","checklist_index":null,"checklist_confidence":0.92,"detected_company_name":null,"company_name":null,"company_confidence":0,"company_evidence":[],"reason":"与当前审计清单无关"}}]"""


def _call_file_classifier(checklist_items, scanned_items, companies, provider, api_key, model, base_url, initial_results=None, config=None):
    response_text, usage = _chat_completion_text(
        "你是审计资料文件分类助手，必须逐文件分类并严格返回JSON。",
        _build_file_classification_prompt(checklist_items, scanned_items, companies, initial_results),
        provider, api_key, model, base_url, config,
    )
    results = _parse_llm_response(response_text)
    return results, usage


def llm_classify_files_with_content(checklist_items, scanned_items, config, companies=None):
    """将每个未归属文件按内容分类到清单项及公司，低置信度结果自动复核。"""
    api_key = config.get("api_key", "")
    provider = config.get("provider", "qwen-flash")
    preset = MODEL_PRESETS.get(provider, MODEL_PRESETS["qwen-flash"])
    model = config.get("model") or preset["model"]
    base_url = config.get("base_url") or preset["base_url"]
    if not api_key and provider != "ollama":
        raise ValueError("请提供API Key")

    results, usage = _call_file_classifier(
        checklist_items, scanned_items, companies, provider, api_key, model, base_url, config=config
    )
    valid_file_ids = {item["file_id"] for item in scanned_items}
    results = [r for r in results if r.get("file_id") in valid_file_ids]
    for result in results:
        result["source"] = "flash"

    recheck_threshold = float(config.get("content_auto_threshold", 0.75))
    company_recheck_threshold = float(config.get("company_auto_threshold", 0.85))
    low_ids = {
        r.get("file_id") for r in results
        if r.get("checklist_confidence", r.get("confidence", 0)) < recheck_threshold
        or (companies and float(r.get("company_confidence", 0) or 0) < company_recheck_threshold)
    }
    returned_ids = {r.get("file_id") for r in results}
    low_ids.update(valid_file_ids - returned_ids)
    stages = {"flash": {"usage": usage, "results": list(results)}}
    if low_ids:
        recheck_items = [item for item in scanned_items if item["file_id"] in low_ids]
        previous = [r for r in results if r.get("file_id") in low_ids]
        checked, checked_usage = _call_file_classifier(
            checklist_items, recheck_items, companies, provider, api_key, model, base_url, previous, config
        )
        by_file = {r.get("file_id"): r for r in results}
        for result in checked:
            if result.get("file_id") not in low_ids:
                continue
            result["source"] = "recheck"
            by_file[result["file_id"]] = result
        results = [by_file[file_id] for file_id in valid_file_ids if file_id in by_file]
        stages["recheck"] = {"usage": checked_usage, "results": checked}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage[key] = usage.get(key, 0) + checked_usage.get(key, 0)

    return {"results": results, "usage": usage, "stages": stages}


def _parse_llm_response(response_text):
    """解析LLM返回的JSON结果"""
    # 尝试提取JSON部分（LLM可能返回多余文字）
    json_match = re.search(r'\[[\s\S]*\]', response_text)
    if json_match:
        return json.loads(json_match.group())
    raise ValueError("无法从LLM响应中提取JSON结果")


def _call_llm(unmatched_items, scanned_names, provider, api_key, model, base_url):
    """调用指定 LLM 执行匹配，返回 (results, usage)"""
    prompt = _build_prompt(unmatched_items, scanned_names)
    response_text, usage = _chat_completion_text(
        "你是审计文件匹配助手，请严格按照要求返回JSON格式结果。",
        prompt, provider, api_key, model, base_url,
    )
    results = _parse_llm_response(response_text)
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
        # 如果主模型是 flash，复核自动升级到同厂商的 plus，提升复核质量
        FLASH_TO_PLUS = {
            "qwen-flash": "qwen-plus",
            "openai-gpt4o-mini": "openai-gpt4o",
        }
        if provider in FLASH_TO_PLUS and config.get("recheck_provider") is None:
            recheck_provider = FLASH_TO_PLUS[provider]
            recheck_preset = MODEL_PRESETS[recheck_provider]
            recheck_model = recheck_preset["model"]
            recheck_base_url = recheck_preset["base_url"]
        else:
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
