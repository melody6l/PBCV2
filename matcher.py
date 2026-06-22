"""文件匹配引擎 - 支持精确匹配和模糊匹配（文件+文件夹）"""

import os
import re

from excel_handler import normalize_item_name


def extract_keywords(name):
    """从文件名中提取核心关键词，去除序号、特殊字符等"""
    # 移除常见序号前缀：如 "1.", "1)", "(1)", "一、", "第1项" 等
    cleaned = re.sub(r'^[\(（]?\d+[\)）]?[.、\s]*', '', name)
    cleaned = re.sub(r'^第\d+项[、\s]*', '', cleaned)
    cleaned = re.sub(r'^[一二三四五六七八九十]+[、\s]*', '', cleaned)
    # 移除括号内的序号说明
    cleaned = re.sub(r'[\(（][^)）]*[\)）]', '', cleaned)
    # 移除特殊字符，保留中文、字母、数字
    cleaned = re.sub(r'[^\w一-鿿]+', ' ', cleaned)
    # 拆分为关键词列表
    keywords = [kw.strip() for kw in cleaned.split() if kw.strip()]
    return keywords


def exact_match(checklist_name, scanned_files, scanned_folders):
    """精确匹配：清单名称与实际文件/文件夹名完全一致（忽略文件扩展名）"""
    checklist_base = os.path.splitext(checklist_name)[0]
    matches = []
    for path in scanned_files:
        name = os.path.basename(path)
        base = os.path.splitext(name)[0]
        if checklist_base == base:
            matches.append(path)
    for path in scanned_folders:
        name = os.path.basename(path)
        if checklist_base == name:
            matches.append(path)
    return matches


def fuzzy_match(checklist_name, scanned_files, scanned_folders):
    """模糊匹配：清单关键词出现在实际文件/文件夹名中即匹配"""
    keywords = extract_keywords(checklist_name)
    if not keywords:
        return []
    matches = []
    for path in scanned_files:
        name = os.path.basename(path)
        base = os.path.splitext(name)[0]
        if all(kw.lower() in base.lower() for kw in keywords):
            matches.append(path)
    for path in scanned_folders:
        name = os.path.basename(path)
        if all(kw.lower() in name.lower() for kw in keywords):
            matches.append(path)
    return matches


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


def match_files(checklist_items, scanned_files, scanned_folders, mode="fuzzy", prev_results=None, company_names=None):
    """
    对清单中的每一项执行匹配（同时匹配文件和文件夹）

    参数:
        checklist_items: 清单名称列表
        scanned_files: 扫描得到的文件路径列表
        scanned_folders: 扫描得到的文件夹路径列表
        mode: "exact" 或 "fuzzy"
        company_names: 公司简称列表（用于检测文件夹内的公司子文件夹和文件名中的公司名）

    返回:
        匹配结果列表，每项包含:
        - index: 序号
        - checklist_name: 清单中的名称
        - status: "已获取" 或 "未获取"
        - matched_files: 匹配到的路径列表（文件或文件夹）
        - matched_names: 匹配到的名称列表
        - matched_types: 匹配到的类型列表（"文件" 或 "文件夹"）
        - company_coverage: {company: {"files": [paths], "folders": [paths]}}
    """
    results = []
    match_func = exact_match if mode == "exact" else fuzzy_match

    history = _history_lookup(prev_results)
    used_paths = set()
    for result in history.values():
        if result.get("status") == "已获取":
            for path in result.get("matched_files", []) or []:
                used_paths.add(path)

    for i, item in enumerate(checklist_items):
        checklist_name = _item_name(item)
        item_key = _item_key(item)
        prev_result = history.get(item_key)

        if prev_result and prev_result.get("status") == "已获取":
            kept = dict(prev_result)
            kept["index"] = i + 1
            results.append(kept)
            continue

        matched = match_func(checklist_name, scanned_files, scanned_folders)
        matched = [path for path in matched if path not in used_paths]

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

        status = "已获取" if matched else "未获取"
        matched_names = [os.path.basename(f) for f in matched]
        matched_types = []
        for path in matched:
            matched_types.append("文件夹" if os.path.isdir(path) else "文件")
            used_paths.add(path)
        results.append({
            "index": i + 1,
            "checklist_name": checklist_name,
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
