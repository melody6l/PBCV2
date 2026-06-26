"""文件匹配引擎 - 基于关键词的模糊匹配（文件+文件夹）"""

import os
import re

from excel_handler import normalize_item_name


def extract_keywords(name):
    """从文件名中提取核心关键词和可选关键词（括号内信息）

    返回 (core_keywords, optional_keywords) 两个列表：
    - core_keywords：去除括号后提取的关键词，用于宽松匹配
    - optional_keywords：括号内有实际信息量的关键词（过滤纯数字/序号），用于精确区分

    分层匹配策略：
    1. 优先用 core + optional 全量关键词精确匹配（区分 CN02 vs CN03、合并 vs 单体）
    2. 若无精确匹配，回退到仅 core 关键词宽松匹配（处理括号内仅英文解释等场景）
    """
    cleaned = name.strip()

    # 移除常见序号前缀：如 "1.", "1)", "(1)", "一、", "第1项" 等
    cleaned = re.sub(r'^[\(（]?\d+[\)）]?[.、\s]*', '', cleaned)
    cleaned = re.sub(r'^第\d+项[、\s]*', '', cleaned)
    cleaned = re.sub(r'^[一二三四五六七八九十]+[、\s]*', '', cleaned)

    # 提取括号内的内容作为可选关键词（在移除括号之前提取）
    optional_parts = re.findall(r'[\(（]([^)）]*)[\)）]', cleaned)

    # 移除括号及其内容，得到核心文本
    core_text = re.sub(r'[\(（][^)）]*[\)）]', '', cleaned)
    # 移除特殊字符，保留中文、字母、数字
    core_text = re.sub(r'[^\w一-鿿]+', ' ', core_text)
    core_keywords = [kw.strip() for kw in core_text.split() if kw.strip()]

    # 处理括号内内容：拆分为关键词，过滤纯数字和序号噪声
    optional_keywords = []
    for part in optional_parts:
        part = part.strip()
        if not part:
            continue
        # 过滤纯数字/纯序号（如 "1"、"一"、"2)"等）
        if re.match(r'^[\d\s.\-、/]+$', part):
            continue
        # 过滤纯中文序号
        if re.match(r'^[一二三四五六七八九十百千万亿\s]+$', part):
            continue
        # 过滤单字符噪声
        if len(part) <= 1:
            continue
        # 拆分为子关键词（中英文数字混排）
        sub_kws = re.findall(r'[\w]+|[一-鿿]+', part)
        for kw in sub_kws:
            kw = kw.strip()
            if kw and (len(kw) > 1 or kw.isalpha()):
                optional_keywords.append(kw)

    return core_keywords, optional_keywords


def fuzzy_match(checklist_name, scanned_files, scanned_folders):
    """模糊匹配：分层匹配策略

    第一层：core + optional 全量关键词精确匹配（区分 CN02 vs CN03、合并 vs 单体）
    第二层：仅 core 关键词宽松匹配（处理括号内仅英文解释等场景）
    """
    core_keywords, optional_keywords = extract_keywords(checklist_name)
    all_keywords = core_keywords + optional_keywords

    if not core_keywords:
        return []

    def _match_paths(paths, keywords, get_name_func):
        """在路径列表中查找所有关键词都匹配的路径"""
        results = []
        for path in paths:
            name = get_name_func(path)
            if all(kw.lower() in name.lower() for kw in keywords):
                results.append(path)
        return results

    def _get_file_base(path):
        return os.path.splitext(os.path.basename(path))[0]

    def _get_folder_name(path):
        return os.path.basename(path)

    # 第一层：全量关键词精确匹配
    file_matches = _match_paths(scanned_files, all_keywords, _get_file_base)
    folder_matches = _match_paths(scanned_folders, all_keywords, _get_folder_name)

    # 如果精确匹配无结果且有可选关键词，回退到核心关键词宽松匹配
    if not file_matches and not folder_matches and optional_keywords:
        file_matches = _match_paths(scanned_files, core_keywords, _get_file_base)
        folder_matches = _match_paths(scanned_folders, core_keywords, _get_folder_name)

    return file_matches + folder_matches


def _item_name(item):
    if isinstance(item, dict):
        return item.get("name") or item.get("checklist_name") or ""
    return str(item)


def _item_key(item):
    if isinstance(item, dict):
        return item.get("row_uid") or item.get("source_key") or normalize_item_name(_item_name(item))
    return normalize_item_name(_item_name(item))


def _history_lookup(prev_results):
    lookup = {}
    for result in prev_results or []:
        key = _item_key(result)
        if key:
            lookup[key] = result
    return lookup


def _find_company_in_path(path, company_names):
    """沿路径向上查找父目录中是否包含公司名"""
    current = os.path.dirname(path)
    while current:
        dirname = os.path.basename(current)
        if dirname in company_names:
            return dirname
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def _find_company_in_filename(filename, company_names):
    """从文件名中查找公司名"""
    name_lower = os.path.splitext(filename)[0].lower()
    for company in company_names:
        if company.lower() in name_lower:
            return company
    return None


def _merge_company_coverage(result, new_paths, company_names):
    """增量合并模式：为新匹配到的文件检测公司归属，合并到已有 company_coverage 中。"""
    coverage = result.get("company_coverage") or {}
    for path in new_paths:
        is_dir = os.path.isdir(path)
        assigned = False
        if is_dir:
            dirname = os.path.basename(path)
            # 检查是否是公司文件夹本身
            if dirname in company_names:
                coverage.setdefault(dirname, {"files": [], "folders": []})
                if path not in coverage[dirname]["folders"]:
                    coverage[dirname]["folders"].append(path)
                assigned = True
            # 检查文件夹内是否有公司子文件夹
            if not assigned:
                try:
                    subdirs = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
                    for cn in company_names:
                        if cn in subdirs:
                            coverage.setdefault(cn, {"files": [], "folders": []})
                            if path not in coverage[cn]["folders"]:
                                coverage[cn]["folders"].append(path)
                            assigned = True
                except Exception:
                    pass
            # 检查文件夹名称是否包含公司名
            if not assigned:
                for cn in company_names:
                    if cn.lower() in dirname.lower():
                        coverage.setdefault(cn, {"files": [], "folders": []})
                        if path not in coverage[cn]["folders"]:
                            coverage[cn]["folders"].append(path)
                        assigned = True
        else:
            basename = os.path.basename(path)
            cn = _find_company_in_path(path, company_names)
            if cn:
                coverage.setdefault(cn, {"files": [], "folders": []})
                if path not in coverage[cn]["files"]:
                    coverage[cn]["files"].append(path)
                assigned = True
            else:
                cn = _find_company_in_filename(basename, company_names)
                if cn:
                    coverage.setdefault(cn, {"files": [], "folders": []})
                    if path not in coverage[cn]["files"]:
                        coverage[cn]["files"].append(path)
                    assigned = True
    result["company_coverage"] = coverage


def match_files(checklist_items, scanned_files, scanned_folders, prev_results=None, company_names=None, merge_mode=False):
    """
    对清单中的每一项执行模糊匹配（同时匹配文件和文件夹）

    参数:
        checklist_items: 清单名称列表
        scanned_files: 扫描得到的文件路径列表
        scanned_folders: 扫描得到的文件夹路径列表
        company_names: 公司简称列表（用于检测文件夹内的公司子文件夹和文件名中的公司名）
        merge_mode: 增量合并模式。为 True 时，已匹配的条目也会重新匹配，
                    如果发现新文件则追加到已有结果中，不覆盖原有匹配。

    返回:
        匹配结果列表，每项包含:
        - index: 序号
        - checklist_name: 清单中的名称
        - status: "已获取" 或 "未匹配"
        - matched_files: 匹配到的路径列表（文件或文件夹）
        - matched_names: 匹配到的名称列表
        - matched_types: 匹配到的类型列表（"文件" 或 "文件夹"）
        - company_coverage: {company: {"files": [paths], "folders": [paths]}}
        - newly_found: (仅 merge_mode) 本次新发现的匹配文件数
    """
    results = []
    fuzzy_match_func = fuzzy_match

    history = _history_lookup(prev_results)
    for i, item in enumerate(checklist_items):
        checklist_name = _item_name(item)
        item_key = _item_key(item)
        prev_result = history.get(item_key)

        # 增量合并模式：已匹配项也重新匹配，追加新文件
        if merge_mode and prev_result and prev_result.get("status") == "已获取":
            kept = dict(prev_result)
            kept["index"] = i + 1
            # 对全量扫描文件重新做匹配
            newly_matched = fuzzy_match_func(checklist_name, scanned_files, scanned_folders)
            # 找出之前没匹配过的新文件
            existing_paths = set(kept.get("matched_files", []))
            new_paths = [p for p in newly_matched if p not in existing_paths]
            if new_paths:
                kept["matched_files"] = kept.get("matched_files", []) + new_paths
                kept["matched_names"] = kept.get("matched_names", []) + [os.path.basename(p) for p in new_paths]
                kept["matched_types"] = kept.get("matched_types", []) + [
                    "文件夹" if os.path.isdir(p) else "文件" for p in new_paths
                ]
                kept["match_count"] = len(kept["matched_files"])
                kept["newly_found"] = len(new_paths)
                # 更新公司覆盖信息：为新文件检测公司归属
                if company_names and "company_coverage" in kept:
                    _merge_company_coverage(kept, new_paths, company_names)
            else:
                kept["newly_found"] = 0
            results.append(kept)
            continue

        # 非合并模式：已匹配项直接保留
        if not merge_mode and prev_result and prev_result.get("status") == "已获取":
            kept = dict(prev_result)
            kept["index"] = i + 1
            results.append(kept)
            continue

        matched = fuzzy_match_func(checklist_name, scanned_files, scanned_folders)

        # 检查匹配项中的公司覆盖情况
        company_coverage = {}  # {company: {"files": [...], "folders": [...]}}
        if matched and company_names:
            for path in matched:
                is_dir = os.path.isdir(path)
                if is_dir:
                    # Pattern A：文件夹内有公司子文件夹
                    has_company_subdirs = False
                    try:
                        subdirs = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
                        for company in company_names:
                            if company in subdirs:
                                if company not in company_coverage:
                                    company_coverage[company] = {"files": [], "folders": []}
                                company_coverage[company]["folders"].append(path)
                                has_company_subdirs = True
                    except Exception:
                        pass

                    # 文件夹本身也可能是公司文件夹，检查其父目录
                    dirname = os.path.basename(path)
                    if dirname in company_names and dirname not in company_coverage:
                        company_coverage[dirname] = {"files": [], "folders": []}
                        company_coverage[dirname]["folders"].append(path)
                        has_company_subdirs = True

                    # 如果文件夹没有公司子文件夹，检查文件夹名称是否包含公司名
                    if not has_company_subdirs:
                        for company in company_names:
                            if company.lower() in dirname.lower():
                                if company not in company_coverage:
                                    company_coverage[company] = {"files": [], "folders": []}
                                company_coverage[company]["folders"].append(path)
                                has_company_subdirs = True

                    # 如果还是没有公司归属，检查文件夹内的文件
                    if not has_company_subdirs:
                        try:
                            for root, dirs, files in os.walk(path):
                                for fname in files:
                                    if fname.startswith(".") or fname.startswith("~"):
                                        continue
                                    company = _find_company_in_filename(fname, company_names)
                                    if company:
                                        if company not in company_coverage:
                                            company_coverage[company] = {"files": [], "folders": []}
                                        full_path = os.path.join(root, fname)
                                        if full_path not in company_coverage[company]["files"]:
                                            company_coverage[company]["files"].append(full_path)
                        except Exception:
                            pass
                else:
                    # Pattern B：文件名或父目录包含公司名
                    basename = os.path.basename(path)

                    # 先检查父目录
                    company = _find_company_in_path(path, company_names)
                    if company:
                        if company not in company_coverage:
                            company_coverage[company] = {"files": [], "folders": []}
                        company_coverage[company]["files"].append(path)
                    else:
                        # 再检查文件名
                        company = _find_company_in_filename(basename, company_names)
                        if company:
                            if company not in company_coverage:
                                company_coverage[company] = {"files": [], "folders": []}
                            company_coverage[company]["files"].append(path)

        status = "已获取" if matched else "未匹配"
        matched_names = [os.path.basename(f) for f in matched]
        matched_types = []
        for path in matched:
            matched_types.append("文件夹" if os.path.isdir(path) else "文件")
        results.append({
            "index": i + 1,
            "checklist_name": checklist_name,
            "pbc_name": item.get("pbc_name", "") if isinstance(item, dict) else "",
            "row_uid": item.get("row_uid", "") if isinstance(item, dict) else "",
            "source_key": item.get("source_key", normalize_item_name(checklist_name)) if isinstance(item, dict) else normalize_item_name(checklist_name),
            "status": status,
            "matched_files": matched,
            "matched_names": matched_names,
            "matched_types": matched_types,
            "match_count": len(matched),
            "required_count": 1,
            "company_coverage": company_coverage,
        })
    return results
