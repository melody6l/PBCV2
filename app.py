"""审计文件匹配工具 - Flask主应用"""

import os
import sys
import re
import shutil
import json
import uuid
import urllib.parse
import time
import subprocess
from flask import Flask, request, jsonify, render_template, send_file, make_response
from path_utils import get_bundle_path, get_data_dir
from matcher import (
    match_files, explain_fuzzy_match, _find_company_in_path, _find_company_in_filename,
    _merge_company_coverage,
)
from excel_handler import normalize_item_name, export_checklist_two_sheets, build_browse_items
from llm_matcher import llm_match, llm_classify_files_with_content
from content_reader import extract_contents, get_progress, reset_progress
from template_handler import (
    read_template, generate_checklist, read_user_checklist,
    generate_checklist_from_memory, get_company_names_from_session,
)
from project_manager import (
    list_projects, load_project, save_project, create_project, delete_project,
)
from session_manager import get_session_store, create_fresh_state

app = Flask(__name__)
# 模板和静态文件始终从打包资源目录读取（开发环境即项目根目录）
app.template_folder = get_bundle_path("templates")
app.static_folder = get_bundle_path("static")
# 上传文件存入用户数据目录
app.config["UPLOAD_FOLDER"] = get_data_dir("uploads")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


# ====== 会话隔离（替代原来的全局 state） ======

session_store = get_session_store()


def get_session():
    """从请求头 X-Session-Id 获取或创建当前会话的状态。

    每个浏览器标签页有独立的 session ID，状态完全隔离。
    内存中仅保留最近 20 个活跃会话（LRU 淘汰），冷会话从磁盘惰性加载。
    每次请求后自动防抖保存到磁盘，重启不丢数据。
    """
    sid = request.headers.get("X-Session-Id", "")
    if not sid:
        sid = uuid.uuid4().hex
    state = session_store.get(sid)
    session_store.schedule_save(sid)
    return state


def _state():
    """便捷访问：获取当前会话的状态（兼容旧代码风格）。"""
    return get_session()


def _relative_log_path(path, root):
    """返回适合展示的相对路径；路径不在扫描根目录时退化为文件名。"""
    if not path:
        return ""
    try:
        relative = os.path.relpath(path, root) if root else os.path.basename(path)
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            return os.path.basename(path)
        return relative
    except (TypeError, ValueError):
        return os.path.basename(path)


def _run_log(stage, event, status, *, path="", root="", checklist=None,
             strategy="", confidence=None, duration_ms=None, detail=None):
    """构造不含正文、密钥或提示词的统一运行日志事件。"""
    return {
        "stage": stage,
        "event": event,
        "status": status,
        "file_name": os.path.basename(path) if path else "",
        "relative_path": _relative_log_path(path, root),
        "checklist_index": checklist.get("index") if checklist else None,
        "checklist_name": checklist.get("checklist_name", "") if checklist else "",
        "strategy": strategy,
        "confidence": confidence,
        "duration_ms": duration_ms,
        "detail": detail or {},
    }


def _store_dev_logs(state, logs, reset=False):
    """保存可随项目持久化的运行日志，限制数量避免项目文件无限增长。"""
    current = [] if reset else list(state.get("dev_logs") or [])
    current.extend(logs or [])
    state["dev_logs"] = current[-2000:]


def _resolve_unique_path(paths, matched_name):
    """按文件名解析模型结果；拒绝同名歧义，避免静默选错文件。"""
    matches = [p for p in paths if os.path.basename(p) == matched_name]
    return (matches[0] if len(matches) == 1 else None), matches


# ====== 工具函数 ======

def scan_all(folder_path):
    """扫描文件夹，返回文件和文件夹路径列表。"""
    scanned_files = []
    scanned_folders = []
    for root, dirs, files in os.walk(folder_path):
        for d in dirs:
            if not d.startswith(".") and not d.startswith("~"):
                scanned_folders.append(os.path.join(root, d))
        for f in files:
            if not f.startswith(".") and not f.startswith("~"):
                scanned_files.append(os.path.join(root, f))
    return scanned_files, scanned_folders


def select_native_folder(title="选择文件夹", initial_path=""):
    """调用当前操作系统的原生文件夹选择窗口，取消时返回空字符串。"""
    initial_path = os.path.abspath(initial_path) if initial_path and os.path.isdir(initial_path) else ""

    if sys.platform == "darwin":
        script = r'''
on run argv
    set dialogTitle to item 1 of argv
    set initialPath to item 2 of argv
    try
        if initialPath is not "" then
            set selectedFolder to choose folder with prompt dialogTitle default location POSIX file initialPath
        else
            set selectedFolder to choose folder with prompt dialogTitle
        end if
        return POSIX path of selectedFolder
    on error number -128
        return ""
    end try
end run
'''
        result = subprocess.run(
            ["osascript", "-e", script, title, initial_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "无法打开 macOS 文件夹选择窗口")
        return result.stdout.strip().rstrip("/") or ("/" if result.stdout.strip() == "/" else "")

    if sys.platform.startswith("win"):
        script = r'''
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $env:PBC_FOLDER_DIALOG_TITLE
$dialog.ShowNewFolderButton = $true
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.WindowState = [System.Windows.Forms.FormWindowState]::Minimized
if ($env:PBC_INITIAL_FOLDER -and (Test-Path -LiteralPath $env:PBC_INITIAL_FOLDER)) {
    $dialog.SelectedPath = $env:PBC_INITIAL_FOLDER
}
if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.SelectedPath
}
$owner.Dispose()
$dialog.Dispose()
'''
        env = os.environ.copy()
        env["PBC_FOLDER_DIALOG_TITLE"] = title
        env["PBC_INITIAL_FOLDER"] = initial_path
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "无法打开 Windows 文件夹选择窗口")
        return result.stdout.strip()

    raise RuntimeError("当前系统暂不支持原生文件夹选择窗口")


def calculate_diff(prev_files, new_files, prev_folders, new_folders):
    """计算前后扫描结果差异。"""
    if prev_files is None and prev_folders is None:
        return {"mode": "full_scan"}

    prev_files_set = set(prev_files or [])
    new_files_set = set(new_files or [])
    prev_folders_set = set(prev_folders or [])
    new_folders_set = set(new_folders or [])
    return {
        "mode": "incremental",
        "added_files": sorted(new_files_set - prev_files_set),
        "removed_files": sorted(prev_files_set - new_files_set),
        "added_folders": sorted(new_folders_set - prev_folders_set),
        "removed_folders": sorted(prev_folders_set - new_folders_set),
        "total_added": len(new_files_set - prev_files_set) + len(new_folders_set - prev_folders_set),
        "total_removed": len(prev_files_set - new_files_set) + len(prev_folders_set - new_folders_set),
    }


def build_history_results(checklist):
    """从导入的历史清单重建前端可展示的匹配结果。"""
    results = []
    for i, item in enumerate(checklist.get("items", []), 1):
        matched_files = item.get("matched_files", []) or []
        matched_names = item.get("matched_names", []) or [os.path.basename(p) for p in matched_files]
        matched_types = item.get("matched_types", []) or [
            "文件夹" if os.path.isdir(p) else "文件" for p in matched_files
        ]
        status = item.get("status", "未匹配")
        if status == "未获取":
            status = "未匹配"
        results.append({
            "index": item.get("row_index", i),
            "checklist_name": item.get("name", ""),
            "pbc_name": item.get("pbc_name", ""),
            "row_uid": item.get("row_uid", ""),
            "source_key": item.get("source_key", normalize_item_name(item.get("name", ""))),
            "status": status,
            "matched_files": matched_files,
            "matched_names": matched_names,
            "matched_types": matched_types,
            "match_count": len(matched_files),
            "required_count": 1,
        })
    return results


def result_counts(results):
    matched_count = sum(1 for r in results if r["status"] in ("已获取", "部分获取"))
    partial_count = sum(1 for r in results if r["status"] == "部分获取")
    return matched_count, partial_count


def get_matched_paths(state):
    """收集所有已被匹配引用的路径集合。"""
    matched = set()
    if state["match_results"]:
        for r in state["match_results"]:
            for p in r["matched_files"]:
                matched.add(p)
    return matched


def prune_match_results(results, valid_paths):
    """删除已不存在的文件引用，保留仍有效的历史及人工匹配。"""
    valid = {os.path.normcase(os.path.normpath(path)) for path in valid_paths}
    pruned = []
    for original in results or []:
        result = dict(original)
        paths = result.get("matched_files") or []
        names = result.get("matched_names") or []
        types = result.get("matched_types") or []
        kept_positions = [
            index for index, path in enumerate(paths)
            if os.path.normcase(os.path.normpath(path)) in valid
        ]
        result["matched_files"] = [paths[index] for index in kept_positions]
        result["matched_names"] = [
            names[index] if index < len(names) else os.path.basename(paths[index])
            for index in kept_positions
        ]
        result["matched_types"] = [
            types[index] if index < len(types) else ("文件夹" if os.path.isdir(paths[index]) else "文件")
            for index in kept_positions
        ]
        result["match_count"] = len(result["matched_files"])
        if not result["matched_files"]:
            result["status"] = "未匹配"
            result["company_coverage"] = {}
        else:
            coverage = {}
            for company, company_data in (result.get("company_coverage") or {}).items():
                kept_files = [
                    path for path in company_data.get("files", [])
                    if os.path.normcase(os.path.normpath(path)) in valid
                ]
                kept_folders = [
                    path for path in company_data.get("folders", [])
                    if os.path.normcase(os.path.normpath(path)) in valid
                ]
                if kept_files or kept_folders:
                    coverage[company] = {"files": kept_files, "folders": kept_folders}
            result["company_coverage"] = coverage
        pruned.append(result)
    return pruned


def build_browse_view_items(state):
    """构建以扫描资料为中心的动态层级列表。"""
    return build_browse_items(
        state.get("scanned_files") or [],
        state.get("scanned_folders") or [],
        state.get("scan_root") or "",
        state.get("match_results") or [],
    )


# ====== 页面路由 ======

@app.route("/")
def index():
    return render_template("index.html")


# ====== 系统文件夹选择 ======

@app.route("/api/select-folder", methods=["POST"])
def select_folder():
    """在运行本工具的电脑上打开系统原生文件夹选择窗口。"""
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "选择文件夹")[:80]
    initial_path = str(data.get("initial_path") or "")
    try:
        selected_path = select_native_folder(title, initial_path)
    except Exception as exc:
        return jsonify({"error": str(exc), "fallback": True}), 500
    return jsonify({
        "success": True,
        "cancelled": not bool(selected_path),
        "path": selected_path,
    })


# ====== 扫描 & 匹配 ======

@app.route("/api/scan-folder", methods=["POST"])
def scan_folder():
    """只扫描指定文件夹并记录变化；匹配由用户在下一步明确触发。"""
    s = _state()
    data = request.get_json()
    folder_path = data.get("folder_path", "")
    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({"error": "文件夹路径无效"}), 400

    previous_root = s.get("scan_root") or ""
    same_root = bool(previous_root) and os.path.normcase(os.path.abspath(previous_root)) == os.path.normcase(os.path.abspath(folder_path))
    prev_files = s.get("scanned_files") if same_root else None
    prev_folders = s.get("scanned_folders") if same_root else None
    if prev_files is None and prev_folders is None:
        prev_files = (s.get("previous_scanned_files") or None) if same_root else None
        prev_folders = (s.get("previous_scanned_folders") or None) if same_root else None
    scanned_files, scanned_folders = scan_all(folder_path)
    diff = calculate_diff(prev_files, scanned_files, prev_folders, scanned_folders)

    s["previous_scanned_files"] = list(prev_files or [])
    s["previous_scanned_folders"] = list(prev_folders or [])
    s["scanned_files"] = scanned_files
    s["scanned_folders"] = scanned_folders
    s["scan_root"] = folder_path
    if diff["mode"] == "full_scan":
        pending_files = scanned_files
        pending_folders = scanned_folders
        match_required = True
    else:
        pending_files = diff["added_files"]
        pending_folders = diff["added_folders"]
        match_required = bool(diff["total_added"] or diff["total_removed"])
    if not s.get("match_results"):
        match_required = True
        pending_files = scanned_files
        pending_folders = scanned_folders
    s["pending_match_files"] = pending_files
    s["pending_match_folders"] = pending_folders
    s["scan_needs_match"] = match_required
    s["last_scan_diff"] = diff

    return jsonify({
        "success": True,
        "scanned_count": len(scanned_files) + len(scanned_folders),
        "file_count": len(scanned_files),
        "folder_count": len(scanned_folders),
        "diff": diff,
        "match_required": match_required,
        "has_previous_results": bool(s.get("match_results")),
        "pending_file_count": len(pending_files),
        "pending_folder_count": len(pending_folders),
        "root_path": folder_path,
    })


@app.route("/api/match", methods=["POST"])
def do_match():
    """执行模糊匹配"""
    s = _state()
    data = request.get_json()
    incremental = data.get("incremental", False)

    if not s["checklist"]:
        return jsonify({"error": "请先上传清单文件"}), 400
    if not s["scanned_files"] and not s.get("scanned_folders"):
        return jsonify({"error": "请先扫描目标文件夹"}), 400

    prev_results = None
    candidate_files = s["scanned_files"]
    candidate_folders = s.get("scanned_folders", [])
    if incremental and s.get("match_results"):
        all_valid_paths = list(s["scanned_files"]) + list(s.get("scanned_folders", []))
        prev_results = prune_match_results(s["match_results"], all_valid_paths)
        candidate_files = s.get("pending_match_files") or []
        candidate_folders = s.get("pending_match_folders") or []
    # 获取公司列表
    company_names = get_company_names_from_session(s)

    started = time.perf_counter()
    results = match_files(
        s["checklist"]["items"],
        candidate_files,
        candidate_folders,
        prev_results=prev_results,
        company_names=company_names,
        merge_mode=bool(prev_results),
    )
    s["match_results"] = results
    s["checklist"]["has_previous_results"] = True
    s["pending_match_files"] = []
    s["pending_match_folders"] = []
    s["scan_needs_match"] = False
    matched_count, partial_count = result_counts(results)
    duration_ms = round((time.perf_counter() - started) * 1000)
    root = s.get("scan_root", "")
    dev_logs = []
    used_paths = set()
    for result in results:
        matched_types = result.get("matched_types") or []
        for position, path in enumerate(result.get("matched_files") or []):
            # 运行日志以文件为统计单位；目录匹配不进入日志面板。
            if (position < len(matched_types) and matched_types[position] == "文件夹") or os.path.isdir(path):
                continue
            used_paths.add(os.path.normpath(path))
            diagnosis = explain_fuzzy_match(
                result.get("checklist_name", ""), path, os.path.isdir(path)
            )
            # 历史/手动结果在增量匹配中可能没有规则命中依据。
            strategy = diagnosis["strategy"] if diagnosis else "existing_result"
            keywords = diagnosis["matched_keywords"] if diagnosis else []
            dev_logs.append(_run_log(
                "l1", "match_accepted", "success", path=path, root=root,
                checklist=result, strategy=strategy, duration_ms=duration_ms,
                detail={"matched_keywords": keywords},
            ))
    for path in (s.get("scanned_files") or []):
        if os.path.normpath(path) not in used_paths:
            dev_logs.append(_run_log(
                "l1", "file_unmatched", "warning", path=path, root=root,
                strategy="keyword", duration_ms=duration_ms,
            ))
    for result in results:
        if result.get("status") in ("未匹配", "待匹配"):
            dev_logs.append(_run_log(
                "l1", "checklist_unmatched", "warning", root=root,
                checklist=result, strategy="keyword", duration_ms=duration_ms,
            ))
    _store_dev_logs(s, dev_logs, reset=True)
    return jsonify({
        "success": True,
        "results": results,
        "matched_count": matched_count,
        "partial_count": partial_count,
        "total": len(results),
        "root_path": s.get("scan_root", ""),
        "dev_logs": dev_logs,
    })


@app.route("/api/reset-state", methods=["POST"])
def reset_state():
    """重置当前会话的所有状态。"""
    sid = request.headers.get("X-Session-Id", "")
    if sid:
        session_store.reset(sid)
    return jsonify({"success": True})


# ====== PBC需求清单生成 ======

@app.route("/api/template-info", methods=["GET"])
def template_info():
    """获取模板信息（科目列表和资料项）"""
    try:
        info = read_template()
        return jsonify({"success": True, **info})
    except Exception as e:
        return jsonify({"error": f"读取模板失败: {str(e)}"}), 500


@app.route("/api/generate-checklist", methods=["POST"])
def gen_checklist():
    """根据选择的科目生成PBC需求清单"""
    s = _state()
    data = request.get_json()
    subjects = data.get("subjects", [])
    company_full = data.get("company_full_name", "")
    company_short = data.get("company_short_name", "")
    if not subjects:
        return jsonify({"error": "请至少选择一个科目"}), 400

    try:
        file_path = generate_checklist(subjects, company_full, company_short)
        s["checklist_file_path"] = file_path

        # 读取生成的清单
        tpl_data = read_user_checklist(file_path)
        s["checklist_template"] = tpl_data

        # 同时构建兼容匹配流程的 checklist 数据
        items_for_match = []
        for it in tpl_data["items"]:
            name = it.get("demand_name") or it.get("pbc_name") or ""
            if name:
                it["row_uid"] = it.get("row_uid") or uuid.uuid4().hex
                it["source_key"] = it.get("source_key") or normalize_item_name(name)
                items_for_match.append({
                    "name": name,
                    "pbc_name": it.get("pbc_name", ""),
                    "source_key": it["source_key"],
                    "row_uid": it["row_uid"],
                })
        s["checklist"] = {
            "headers": tpl_data.get("headers", []),
            "data": [],
            "name_col_index": 3,
            "items": items_for_match,
            "has_previous_results": False,
        }
        s["match_results"] = None
        s["pending_match_files"] = list(s.get("scanned_files") or [])
        s["pending_match_folders"] = list(s.get("scanned_folders") or [])
        s["scan_needs_match"] = bool(s.get("scanned_files") or s.get("scanned_folders"))

        return jsonify({
            "success": True,
            "file_path": file_path,
            "items": tpl_data["items"],
            "companies": tpl_data["companies"],
            "company_names": tpl_data["company_names"],
            "headers": tpl_data["headers"],
            "total": len(tpl_data["items"]),
        })
    except Exception as e:
        return jsonify({"error": f"生成清单失败: {str(e)}"}), 500


@app.route("/api/download-checklist", methods=["GET"])
def download_checklist():
    """下载PBC需求清单（从内存状态生成，确保包含最新修改）"""
    s = _state()
    tpl_data = s.get("checklist_template")
    file_path = s.get("checklist_file_path")

    if tpl_data:
        for item in tpl_data.get("items", []):
            demand_name = item.get("demand_name") or item.get("pbc_name") or ""
            item.setdefault("row_uid", uuid.uuid4().hex)
            if not item.get("row_uid"):
                item["row_uid"] = uuid.uuid4().hex
            item["source_key"] = item.get("source_key") or normalize_item_name(demand_name)
        # 从内存状态生成 Excel（包含所有单元格状态修改）
        output_path = generate_checklist_from_memory(tpl_data)
        return send_file(output_path, as_attachment=True, download_name="PBC需求清单_待填写.xlsx")

    if file_path and os.path.exists(file_path):
        return send_file(file_path, as_attachment=True, download_name="PBC需求清单_待填写.xlsx")

    return jsonify({"error": "请先生成PBC需求清单"}), 400


@app.route("/api/upload-checklist-v2", methods=["POST"])
def upload_checklist_v2():
    """上传用户填写后的PBC需求清单，解析为预览数据"""
    s = _state()
    if "file" not in request.files:
        return jsonify({"error": "未提供文件"}), 400
    file = request.files["file"]
    if not file.filename.endswith(".xlsx"):
        return jsonify({"error": "仅支持.xlsx格式文件"}), 400

    save_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
    file.save(save_path)
    s["checklist_file_path"] = save_path

    try:
        tpl_data = read_user_checklist(save_path)
        s["checklist_template"] = tpl_data

        # 同时构建兼容旧流程的匹配数据
        items = []
        for it in tpl_data["items"]:
            name = it.get("demand_name") or it.get("pbc_name") or ""
            if name:
                it["row_uid"] = it.get("row_uid") or uuid.uuid4().hex
                it["source_key"] = it.get("source_key") or normalize_item_name(name)
                items.append({
                    "name": name,
                    "pbc_name": it.get("pbc_name", ""),
                    "source_key": it["source_key"],
                    "row_uid": it["row_uid"],
                })

        checklist = {
            "headers": tpl_data.get("headers", []),
            "data": [],
            "name_col_index": 3,
            "items": items,
            "has_previous_results": False,
        }
        s["checklist"] = checklist
        s["match_results"] = None
        s["pending_match_files"] = list(s.get("scanned_files") or [])
        s["pending_match_folders"] = list(s.get("scanned_folders") or [])
        s["scan_needs_match"] = bool(s.get("scanned_files") or s.get("scanned_folders"))

        return jsonify({
            "success": True,
            "items": tpl_data["items"],
            "companies": tpl_data["companies"],
            "company_names": tpl_data["company_names"],
            "headers": tpl_data["headers"],
            "total": len(tpl_data["items"]),
        })
    except Exception as e:
        return jsonify({"error": f"读取清单失败: {str(e)}"}), 500


def _build_checklist_merge_preview(state, incoming, mode):
    """比较上传清单和当前清单；冲突只报告，不自动覆盖。"""
    current = state.get("checklist_template") or {}
    current_items = current.get("items") or []
    current_companies = current.get("companies") or []

    by_uid = {item.get("row_uid"): item for item in current_items if item.get("row_uid")}
    by_key = {
        item.get("source_key") or normalize_item_name(item.get("demand_name") or item.get("pbc_name") or ""): item
        for item in current_items
    }
    additions, updates, duplicates, conflicts = [], [], [], []
    seen_incoming = set()
    for item in incoming.get("items") or []:
        name = item.get("demand_name") or item.get("pbc_name") or ""
        key = item.get("source_key") or normalize_item_name(name)
        uid = item.get("row_uid")
        if not name:
            conflicts.append({"type": "需求", "name": "空白需求", "reason": "需求资料和所需PBC均为空"})
            continue
        if key in seen_incoming:
            conflicts.append({"type": "需求", "name": name, "reason": "上传文件内重复"})
            continue
        seen_incoming.add(key)
        existing = (by_uid.get(uid) if mode == "full" and uid else None) or by_key.get(key)
        if mode == "append":
            if existing:
                duplicates.append({"type": "需求", "name": name, "reason": "当前清单已存在"})
            else:
                additions.append(item)
            continue
        if existing:
            changed = any(
                str(existing.get(field, "") or "").strip() != str(item.get(field, "") or "").strip()
                for field in ("subject", "pbc_name", "demand_name")
            )
            if changed:
                updates.append({"existing_row_uid": existing.get("row_uid", ""), "existing_row_index": existing.get("row_index"), "item": item})
            else:
                duplicates.append({"type": "需求", "name": name, "reason": "内容无变化"})
        else:
            additions.append(item)

    existing_by_short = {
        c.get("short_name") or c.get("full_name"): c for c in current_companies
        if c.get("short_name") or c.get("full_name")
    }
    existing_by_full = {c.get("full_name"): c for c in current_companies if c.get("full_name")}
    company_additions = []
    for company in incoming.get("companies") or []:
        full = (company.get("full_name") or "").strip()
        short = (company.get("short_name") or "").strip()
        canonical = short or full
        if not full:
            conflicts.append({"type": "公司", "name": canonical or "空白公司", "reason": "公司全称不能为空"})
        elif canonical in existing_by_short:
            existing = existing_by_short[canonical]
            if existing.get("full_name", "") != full:
                conflicts.append({"type": "公司", "name": canonical, "reason": "简称相同但公司全称不同"})
            else:
                duplicates.append({"type": "公司", "name": canonical, "reason": "当前公司清单已存在"})
        elif full in existing_by_full:
            conflicts.append({"type": "公司", "name": canonical, "reason": "公司全称已存在但简称不同"})
        else:
            company_additions.append(company)

    return {
        "mode": mode,
        "additions": additions,
        "updates": updates,
        "company_additions": company_additions,
        "duplicates": duplicates,
        "conflicts": conflicts,
        "summary": {
            "new_items": len(additions),
            "updated_items": len(updates),
            "new_companies": len(company_additions),
            "duplicates": len(duplicates),
            "conflicts": len(conflicts),
        },
    }


@app.route("/api/checklist-merge/preview", methods=["POST"])
def preview_checklist_merge():
    """解析新版清单并返回合并预览，不修改当前项目。"""
    s = _state()
    if not s.get("checklist_template"):
        return jsonify({"error": "当前还没有清单，请使用“上传已填写的清单”初始化"}), 400
    if "file" not in request.files:
        return jsonify({"error": "请选择要合并的.xlsx文件"}), 400
    file = request.files["file"]
    mode = request.form.get("mode", "full")
    if mode not in ("full", "append"):
        return jsonify({"error": "无效的合并模式"}), 400
    if not file.filename.lower().endswith(".xlsx"):
        return jsonify({"error": "仅支持.xlsx格式文件"}), 400
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], f"merge_{uuid.uuid4().hex}.xlsx")
    file.save(save_path)
    try:
        incoming = read_user_checklist(save_path)
        preview = _build_checklist_merge_preview(s, incoming, mode)
        token = uuid.uuid4().hex
        s["_pending_checklist_merge"] = {"token": token, "preview": preview}
        return jsonify({
            "success": True,
            "token": token,
            "summary": preview["summary"],
            "new_items": [
                item.get("demand_name") or item.get("pbc_name") for item in preview["additions"][:50]
            ],
            "updated_items": [
                entry["item"].get("demand_name") or entry["item"].get("pbc_name")
                for entry in preview["updates"][:50]
            ],
            "new_companies": [
                c.get("short_name") or c.get("full_name") for c in preview["company_additions"][:50]
            ],
            "duplicates": preview["duplicates"][:50],
            "conflicts": preview["conflicts"][:50],
        })
    except Exception as e:
        return jsonify({"error": f"读取合并清单失败: {str(e)}"}), 500


@app.route("/api/checklist-merge/commit", methods=["POST"])
def commit_checklist_merge():
    """确认应用最近一次合并预览，保留现有匹配及公司归属。"""
    s = _state()
    data = request.get_json() or {}
    pending = s.get("_pending_checklist_merge") or {}
    if not pending or data.get("token") != pending.get("token"):
        return jsonify({"error": "合并预览已失效，请重新选择文件"}), 400
    preview = pending["preview"]
    tpl = s["checklist_template"]
    items = tpl.setdefault("items", [])
    companies = tpl.setdefault("companies", [])
    old_company_names = list(tpl.get("company_names") or [])

    for company in preview["company_additions"]:
        companies.append(company)
    company_names = [
        c.get("short_name") or c.get("full_name") for c in companies
        if c.get("short_name") or c.get("full_name")
    ]
    tpl["company_names"] = company_names
    tpl["headers"] = ["序号", "科目", "所需PBC", "需求资料"] + company_names

    by_uid = {item.get("row_uid"): item for item in items if item.get("row_uid")}
    by_index = {item.get("row_index"): item for item in items}
    result_by_index = {r.get("index"): r for r in (s.get("match_results") or [])}
    for entry in preview["updates"]:
        target = by_uid.get(entry.get("existing_row_uid")) or by_index.get(entry.get("existing_row_index"))
        if not target:
            continue
        incoming = entry["item"]
        for field in ("subject", "pbc_name", "demand_name"):
            target[field] = incoming.get(field, "")
        target["source_key"] = normalize_item_name(target.get("demand_name") or target.get("pbc_name") or "")
        result = result_by_index.get(target.get("row_index"))
        if result:
            result["checklist_name"] = target.get("demand_name") or target.get("pbc_name") or ""
            result["pbc_name"] = target.get("pbc_name", "")
            result["source_key"] = target["source_key"]

    next_index = max([int(item.get("row_index") or 0) for item in items] + [0]) + 1
    for incoming in preview["additions"]:
        name = incoming.get("demand_name") or incoming.get("pbc_name") or ""
        new_item = dict(incoming)
        new_item["row_index"] = next_index
        new_item["seq"] = len(items) + 1
        new_item["row_uid"] = incoming.get("row_uid") or uuid.uuid4().hex
        new_item["source_key"] = normalize_item_name(name)
        status = dict(incoming.get("company_status") or {})
        for company_name in company_names:
            status.setdefault(company_name, "N")
        new_item["company_status"] = status
        items.append(new_item)
        if s.get("match_results") is not None:
            s["match_results"].append({
                "index": next_index, "checklist_name": name,
                "pbc_name": new_item.get("pbc_name", ""),
                "row_uid": new_item["row_uid"], "source_key": new_item["source_key"],
                "status": "未匹配", "matched_files": [], "matched_names": [],
                "matched_types": [], "match_count": 0, "required_count": 1,
                "company_coverage": {},
            })
        next_index += 1

    for item in items:
        if not item.get("row_uid"):
            item["row_uid"] = uuid.uuid4().hex
        item["source_key"] = item.get("source_key") or normalize_item_name(
            item.get("demand_name") or item.get("pbc_name") or ""
        )
        status = item.setdefault("company_status", {})
        for company_name in company_names:
            if company_name not in old_company_names:
                status.setdefault(company_name, "N")

    s["checklist"]["items"] = [
        {
            "name": item.get("demand_name") or item.get("pbc_name") or "",
            "pbc_name": item.get("pbc_name", ""),
            "source_key": item.get("source_key", ""),
            "row_uid": item.get("row_uid", ""),
        }
        for item in items
    ]
    s["checklist"]["has_previous_results"] = bool(s.get("match_results"))
    s.pop("_pending_checklist_merge", None)
    return jsonify({
        "success": True,
        "summary": preview["summary"],
        "items": items,
        "companies": companies,
        "company_names": company_names,
        "headers": tpl["headers"],
        "total": len(items),
        "match_results": s.get("match_results") or [],
        "message": "清单更新已合并，现有文件匹配和公司归属已保留",
    })


# ====== 单元格状态 & 行管理 ======

@app.route("/api/update-cell-status", methods=["POST"])
def api_update_cell_status():
    """更新某个单元格的获取状态（仅操作内存，导出时统一写入 Excel）"""
    s = _state()
    data = request.get_json()
    row_index = data.get("row_index")
    if row_index is not None:
        row_index = int(row_index)
    company_name = data.get("company_name")
    status = data.get("status", "Y")

    if not s.get("checklist_template"):
        return jsonify({"error": "请先上传或生成清单"}), 400
    if row_index is None or not company_name:
        return jsonify({"error": "参数不完整"}), 400

    # 只在内存中更新，不再逐个写入 Excel（避免并发覆盖）
    for item in s["checklist_template"]["items"]:
        if item["row_index"] == row_index:
            item["company_status"][company_name] = status
            return jsonify({"success": True})

    return jsonify({"error": f"未找到行: {row_index}"}), 404


@app.route("/api/add-row", methods=["POST"])
def api_add_row():
    """在核对清单中新增一行 PBC 需求（仅操作内存）"""
    s = _state()
    data = request.get_json()
    subject = (data.get("subject") or "").strip()
    pbc_name = (data.get("pbc_name") or "").strip()
    demand_name = (data.get("demand_name") or "").strip() or pbc_name

    if not s.get("checklist_template"):
        return jsonify({"error": "请先上传或生成清单"}), 400
    if not subject or not pbc_name:
        return jsonify({"error": "科目和PBC名称不能为空"}), 400

    company_names = s["checklist_template"].get("company_names", [])

    # 在内存中生成唯一 row_index（比 Excel 最大行号更大，避免冲突）
    existing_rows = [
        it.get("row_index", 0)
        for it in s["checklist_template"]["items"]
    ]
    new_row_index = max(existing_rows) + 1 if existing_rows else 2

    new_item = {
        "row_index": new_row_index,
        "seq": new_row_index,
        "subject": subject,
        "pbc_name": pbc_name,
        "demand_name": demand_name,
        "company_status": {cn: "N" for cn in company_names},
        "_custom": True,
    }

    s["checklist_template"]["items"].append(new_item)

    if s.get("checklist"):
        s["checklist"]["items"].append({
            "name": demand_name,
            "source_key": normalize_item_name(demand_name),
            "row_uid": f"custom_{new_row_index}",
        })

    return jsonify({"success": True, "item": new_item})


@app.route("/api/edit-row", methods=["POST"])
def api_edit_row():
    """编辑核对清单中某行的科目/PBC/需求资料（仅操作内存）"""
    s = _state()
    data = request.get_json()
    row_index = data.get("row_index")
    if row_index is not None:
        row_index = int(row_index)
    field = data.get("field")
    value = (data.get("value") or "").strip()

    if not s.get("checklist_template"):
        return jsonify({"error": "请先上传或生成清单"}), 400
    if row_index is None or not field:
        return jsonify({"error": "缺少 row_index 或 field"}), 400
    if field not in ("subject", "pbc_name", "demand_name"):
        return jsonify({"error": "无效的字段名"}), 400

    # 只在内存中更新
    for item in s["checklist_template"]["items"]:
        if item.get("row_index") == row_index:
            item[field] = value
            if field == "demand_name" and s.get("checklist"):
                for ci in s["checklist"]["items"]:
                    if ci.get("row_uid") == f"custom_{row_index}" or \
                       ci.get("row_uid") == str(row_index):
                        ci["name"] = value
                        ci["source_key"] = normalize_item_name(value)
            break

    return jsonify({"success": True})


@app.route("/api/delete-row", methods=["POST"])
def api_delete_row():
    """删除核对清单中的一行（仅操作内存）"""
    s = _state()
    data = request.get_json()
    row_index = data.get("row_index")
    if row_index is not None:
        row_index = int(row_index)

    if not s.get("checklist_template"):
        return jsonify({"error": "请先上传或生成清单"}), 400
    if row_index is None:
        return jsonify({"error": "缺少 row_index"}), 400

    # 只在内存中删除
    s["checklist_template"]["items"] = [
        it for it in s["checklist_template"]["items"]
        if it.get("row_index") != row_index
    ]

    if s.get("checklist"):
        s["checklist"]["items"] = [
            ci for ci in s["checklist"]["items"]
            if ci.get("row_uid") not in (f"custom_{row_index}", str(row_index))
        ]

    return jsonify({"success": True})


# ====== 导出 ======

@app.route("/api/export-checklist", methods=["GET", "POST"])
def export_checklist():
    """导出包含核对总览和需求列表两个sheet的Excel"""
    s = _state()
    tpl_data = s.get("checklist_template")
    if not tpl_data:
        return jsonify({"error": "无清单数据可导出"}), 400

    items = tpl_data.get("items", [])
    company_names = tpl_data.get("company_names", [])
    match_results = s.get("match_results", [])

    if not items:
        return jsonify({"error": "清单项为空"}), 400

    file_renames = {}
    if request.method == "POST":
        body = request.get_json() or {}
        frontend_statuses = body.get("company_statuses", {})
        file_renames = body.get("file_renames", {})
        if frontend_statuses:
            for item in items:
                ri = str(item.get("row_index"))
                if ri in frontend_statuses:
                    item["company_status"] = frontend_statuses[ri]

    try:
        output_path = export_checklist_two_sheets(
            items,
            company_names,
            match_results,
            file_renames,
            s.get("scanned_files") or [],
            s.get("scanned_folders") or [],
            s.get("scan_root") or "",
        )
        return send_file(output_path, as_attachment=True, download_name="PBC需求清单.xlsx")
    except Exception as e:
        return jsonify({"error": f"导出失败: {str(e)}"}), 500


# ====== 资料浏览 ======

@app.route("/api/browse-view-data", methods=["GET"])
def browse_view_data():
    """返回所有扫描文件的资料浏览数据。"""
    s = _state()
    items, folder_levels = build_browse_view_items(s)
    suggestion_paths = {
        os.path.normpath(item.get("file_path", ""))
        for item in (s.get("content_suggestions") or []) if item.get("file_path")
    }
    suggested_count = sum(
        1 for item in items
        if os.path.normpath(item["path"]) in suggestion_paths
    )
    matched_count = sum(
        1 for item in items
        if item["is_matched"] and os.path.normpath(item["path"]) not in suggestion_paths
    )
    return jsonify({
        "success": True,
        "items": items,
        "folder_levels": folder_levels,
        "total": len(items),
        "matched_count": matched_count,
        "suggested_count": suggested_count,
        "unmatched_count": max(0, len(items) - matched_count - suggested_count),
    })


@app.route("/api/content-suggestions", methods=["GET"])
def content_suggestions():
    """返回待人工确认的内容分类建议及可选清单项。"""
    s = _state()
    checklist_items = [
        {"index": result["index"], "name": result.get("checklist_name", "")}
        for result in (s.get("match_results") or [])
    ]
    return jsonify({
        "success": True,
        "suggestions": s.get("content_suggestions") or [],
        "checklist_items": checklist_items,
        "companies": (s.get("checklist_template") or {}).get("companies", []),
    })


@app.route("/api/content-suggestions/resolve", methods=["POST"])
def resolve_content_suggestion():
    """确认/改选建议归属，或将文件标记为保持未归属。"""
    s = _state()
    data = request.get_json() or {}
    file_path = data.get("file_path", "")
    action = data.get("action", "")
    suggestions = s.get("content_suggestions") or []
    suggestion = next((item for item in suggestions if item.get("file_path") == file_path), None)
    if not suggestion:
        return jsonify({"error": "建议已处理或不存在"}), 404

    if action == "dismiss":
        ignored = s.setdefault("content_ignored_files", [])
        normalized = os.path.normpath(file_path)
        if normalized not in ignored:
            ignored.append(normalized)
    elif action == "assign":
        try:
            checklist_index = int(data.get("checklist_index"))
        except (TypeError, ValueError):
            return jsonify({"error": "请选择清单项"}), 400
        target = next(
            (result for result in (s.get("match_results") or []) if result.get("index") == checklist_index),
            None,
        )
        if not target:
            return jsonify({"error": "未找到指定清单项"}), 404
        company_name = str(data.get("company_name") or "").strip()
        valid_companies = get_company_names_from_session(s)
        if company_name and company_name not in valid_companies:
            return jsonify({"error": "请选择公司清单中的公司"}), 400
        matched_files = target.setdefault("matched_files", [])
        if file_path not in matched_files:
            matched_files.append(file_path)
            target.setdefault("matched_names", []).append(os.path.basename(file_path))
            target.setdefault("matched_types", []).append("文件夹" if os.path.isdir(file_path) else "文件")
        if company_name:
            coverage = target.setdefault("company_coverage", {})
            company_info = coverage.setdefault(company_name, {"files": [], "folders": []})
            if file_path not in company_info["files"]:
                company_info["files"].append(file_path)
        target["status"] = "已获取"
        target["match_count"] = len(matched_files)
        target["matched_source"] = "content_confirmed"
        ignored = s.setdefault("content_ignored_files", [])
        normalized = os.path.normpath(file_path)
        if normalized in ignored:
            ignored.remove(normalized)
    else:
        return jsonify({"error": "无效操作"}), 400

    s["content_suggestions"] = [item for item in suggestions if item.get("file_path") != file_path]
    matched_count = sum(1 for result in (s.get("match_results") or []) if result.get("status") in ("已获取", "部分获取"))
    return jsonify({
        "success": True,
        "remaining": len(s["content_suggestions"]),
        "matched_count": matched_count,
        "match_results": s.get("match_results") or [],
    })


@app.route("/api/folder-tree", methods=["GET"])
def folder_tree():
    """返回指定文件夹的直接子项，标注是否已匹配"""
    s = _state()
    path = request.args.get("path", "")
    path = urllib.parse.unquote(path)
    if not path or not os.path.isdir(path):
        return jsonify({"error": "路径无效"}), 400

    matched_paths = get_matched_paths(s)
    items = []
    for item in os.listdir(path):
        if item.startswith(".") or item.startswith("~"):
            continue
        full = os.path.join(path, item)
        is_dir = os.path.isdir(full)
        items.append({
            "name": item,
            "path": full,
            "is_dir": is_dir,
            "is_matched": full in matched_paths,
            "has_children": is_dir and any(
                not x.startswith(".") and not x.startswith("~")
                for x in os.listdir(full)
            ),
        })
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return jsonify({"items": items})


# ====== 手动匹配 & 公司归属 ======

@app.route("/api/manual-match", methods=["POST"])
def manual_match():
    """手动将文件/文件夹分配到清单中某一行"""
    s = _state()
    data = request.get_json()
    file_path = data.get("file_path")
    index = data.get("index")
    if index is not None:
        index = int(index)

    if not s["match_results"]:
        return jsonify({"error": "尚无匹配结果"}), 400

    matched_result = None
    for r in s["match_results"]:
        if r["index"] == index:
            if file_path in r["matched_files"]:
                return jsonify({"error": "该文件/文件夹已添加，请勿重复操作"}), 400
            r["status"] = "已获取"
            r["matched_files"].append(file_path)
            r["matched_names"].append(os.path.basename(file_path))
            r["matched_types"].append("文件夹" if os.path.isdir(file_path) else "文件")
            r["match_count"] = len(r["matched_files"])
            matched_result = r
            break

    if matched_result is None:
        return jsonify({"error": "未找到指定序号"}), 400

    # 人工分配后同步清除该文件的内容分类建议/忽略标记。
    s["content_suggestions"] = [
        item for item in (s.get("content_suggestions") or [])
        if item.get("file_path") != file_path
    ]
    normalized_path = os.path.normpath(file_path)
    ignored = s.get("content_ignored_files") or []
    if normalized_path in ignored:
        ignored.remove(normalized_path)

    matched_count = sum(1 for r in s["match_results"] if r["status"] in ("已获取", "部分获取"))
    partial_count = sum(1 for r in s["match_results"] if r["status"] == "部分获取")
    return jsonify({
        "success": True,
        "matched_count": matched_count,
        "partial_count": partial_count,
        "total": len(s["match_results"]),
        "match_results": matched_result,
    })


@app.route("/api/assign-company", methods=["POST"])
def assign_company():
    """待认领文件：为已匹配清单项指定公司列表"""
    s = _state()
    data = request.get_json()
    index = data.get("index")
    if index is not None:
        index = int(index)
    company_names = data.get("company_names") or []
    file_path = data.get("file_path") or ""

    if not s["match_results"]:
        return jsonify({"error": "尚无匹配结果"}), 400
    if index is None:
        return jsonify({"error": "缺少序号参数"}), 400

    for r in s["match_results"]:
        if r["index"] == index:
            if file_path:
                if file_path not in (r.get("matched_files") or []):
                    return jsonify({"error": "该文件不属于指定清单项"}), 400
                coverage = r.get("company_coverage") or {}
                for info in coverage.values():
                    for key in ("files", "folders"):
                        if file_path in (info.get(key) or []):
                            info[key].remove(file_path)
                coverage = {
                    company: info for company, info in coverage.items()
                    if (info.get("files") or []) or (info.get("folders") or [])
                }
                for company in company_names:
                    info = coverage.setdefault(company, {"files": [], "folders": []})
                    key = "folders" if os.path.isdir(file_path) else "files"
                    if file_path not in info[key]:
                        info[key].append(file_path)
                r["company_coverage"] = coverage
            elif not company_names:
                r["company_coverage"] = {}
            else:
                new_coverage = {}
                for c in company_names:
                    new_coverage[c] = {"files": [], "folders": []}
                    for fp in r.get("matched_files", []):
                        if os.path.isdir(fp):
                            new_coverage[c]["folders"].append(fp)
                        else:
                            new_coverage[c]["files"].append(fp)
                r["company_coverage"] = new_coverage

            matched_count = sum(1 for x in s["match_results"] if x["status"] in ("已获取", "部分获取"))
            partial_count = sum(1 for x in s["match_results"] if x["status"] == "部分获取")
            return jsonify({
                "success": True,
                "index": index,
                "company_coverage": r["company_coverage"],
                "matched_count": matched_count,
                "partial_count": partial_count,
                "total": len(s["match_results"]),
            })

    return jsonify({"error": "未找到指定序号"}), 400


@app.route("/api/unmatch-file", methods=["POST"])
def unmatch_file():
    """从指定清单行中移除文件关联"""
    s = _state()
    data = request.get_json()
    file_path = data.get("file_path")
    index = data.get("index")
    if index is not None:
        index = int(index)

    if not s["match_results"]:
        return jsonify({"error": "尚无匹配结果"}), 400

    target = None
    for r in s["match_results"]:
        if r["index"] == index:
            target = r
            break

    if target is None:
        return jsonify({"error": "未找到指定序号"}), 400

    # 从 matched_files / matched_names / matched_types 中移除该文件
    if file_path in target.get("matched_files", []):
        idx = target["matched_files"].index(file_path)
        target["matched_files"].pop(idx)
        if idx < len(target.get("matched_names", [])):
            target["matched_names"].pop(idx)
        if idx < len(target.get("matched_types", [])):
            target["matched_types"].pop(idx)

    # 从 company_coverage 各公司的 files/folders 中移除该文件
    for company, info in (target.get("company_coverage") or {}).items():
        if file_path in info.get("files", []):
            info["files"].remove(file_path)
        if file_path in info.get("folders", []):
            info["folders"].remove(file_path)

    # 清理空的 company_coverage 条目
    target["company_coverage"] = {
        k: v for k, v in target.get("company_coverage", {}).items()
        if v.get("files") or v.get("folders")
    }

    # 更新状态和计数
    target["match_count"] = len(target.get("matched_files", []))
    if target["match_count"] == 0:
        target["status"] = "未匹配"

    matched_count = sum(1 for r in s["match_results"] if r["status"] in ("已获取", "部分获取"))
    partial_count = sum(1 for r in s["match_results"] if r["status"] == "部分获取")
    return jsonify({
        "success": True,
        "matched_count": matched_count,
        "partial_count": partial_count,
        "total": len(s["match_results"]),
        "match_results": target,
    })


# ====== 文件整理 ======

@app.route("/api/organize-files", methods=["POST"])
def organize_files():
    """整理已获取的文件：按 科目/需求资料 层级建文件夹，用户指定目标路径"""
    s = _state()
    match_results = s.get("match_results", [])
    if not match_results:
        return jsonify({"error": "无匹配结果"}), 400

    data = request.get_json() or {}
    target_path = data.get("target_path", "").strip()
    file_renames = data.get("file_renames", {})
    if not target_path:
        return jsonify({"error": "请提供放置地址"}), 400
    if not os.path.isdir(target_path):
        try:
            os.makedirs(target_path, exist_ok=True)
        except Exception as e:
            return jsonify({"error": f"目标路径无效: {str(e)}"}), 400

    checklist_template = s.get("checklist_template", {})
    items = checklist_template.get("items", [])
    company_names = checklist_template.get("company_names", [])

    organized = []
    errors = []
    copied_paths = set()

    for result in match_results:
        if result["status"] not in ("已获取", "部分获取"):
            continue

        idx = result["index"]
        # 按 row_index 查找对应清单项（而非数组位置）
        item = next((it for it in items if it.get("row_index") == idx), None)
        if item is None:
            continue

        subject = item.get("subject", "")
        demand_name = item.get("demand_name", "") or item.get("pbc_name", "")
        if not subject or not demand_name:
            continue

        subject_dir = re.sub(r'[<>:"/\\|?*]', '_', subject)
        demand_dir = re.sub(r'[<>:"/\\|?*]', '_', demand_name)
        base_dir = os.path.join(target_path, subject_dir, demand_dir)

        company_coverage = result.get("company_coverage", {})
        matched_files = result.get("matched_files", []) or []
        matched_names = result.get("matched_names", []) or []

        if company_coverage and any(v for v in company_coverage.values()):
            for cName, coverage in company_coverage.items():
                all_paths = (coverage.get("files", []) or []) + (coverage.get("folders", []) or [])
                if not all_paths:
                    continue

                use_subdir = len(all_paths) > 1

                for src_path in all_paths:
                    if src_path in copied_paths or not os.path.exists(src_path):
                        continue
                    copied_paths.add(src_path)

                    src_name = file_renames.get(src_path, os.path.basename(src_path))

                    found = _find_company_in_filename(src_name, [cName] + company_names)
                    if not found:
                        dest_name = f"{cName}_{src_name}"
                    else:
                        dest_name = src_name

                    if use_subdir:
                        dest_dir = os.path.join(base_dir, cName)
                    else:
                        dest_dir = base_dir

                    os.makedirs(dest_dir, exist_ok=True)
                    dest_path = os.path.join(dest_dir, dest_name)

                    try:
                        if os.path.isdir(src_path):
                            shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
                        else:
                            shutil.copy2(src_path, dest_path)
                        organized.append({"source": src_path, "dest": dest_path})
                    except Exception as e:
                        errors.append({"source": src_path, "error": str(e)})

        elif matched_files:
            for i, src_path in enumerate(matched_files):
                if src_path in copied_paths or not os.path.exists(src_path):
                    continue
                copied_paths.add(src_path)
                src_name = file_renames.get(src_path, matched_names[i] if i < len(matched_names) else os.path.basename(src_path))
                dest_path = os.path.join(base_dir, src_name)
                os.makedirs(base_dir, exist_ok=True)
                try:
                    if os.path.isdir(src_path):
                        shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src_path, dest_path)
                    organized.append({"source": src_path, "dest": dest_path})
                except Exception as e:
                    errors.append({"source": src_path, "error": str(e)})

    return jsonify({
        "success": True,
        "organized_count": len(organized),
        "error_count": len(errors),
        "organized": organized[:30],
        "errors": errors[:10],
        "target_root": target_path,
    })


# ====== 文件/文件夹浏览 ======

@app.route("/api/open", methods=["GET"])
def open_file():
    """通过Flask中转打开本地文件或浏览文件夹"""
    path = request.args.get("path", "")
    path = urllib.parse.unquote(path)
    if not path or not os.path.exists(path):
        return jsonify({"error": "路径不存在"}), 404

    if os.path.isdir(path):
        items = []
        for item in os.listdir(path):
            if item.startswith(".") or item.startswith("~"):
                continue
            full = os.path.join(path, item)
            items.append({
                "name": item,
                "path": full,
                "is_dir": os.path.isdir(full),
                "size": os.path.getsize(full) if os.path.isfile(full) else None,
            })
        return render_template("folder_view.html", folder_path=path, items=items)
    else:
        directory = os.path.dirname(path)
        filename = os.path.basename(path)
        return send_file(path, as_attachment=False)


@app.route("/api/llm-match", methods=["POST"])
def do_llm_match():
    """使用LLM辅助匹配未识别的清单项"""
    s = _state()
    import os as _os
    config = request.get_json()
    provider = config.get("provider", "")
    api_key = config.get("api_key", "")

    if not provider:
        return jsonify({"error": "请选择模型"}), 400
    if not api_key and provider != "ollama":
        return jsonify({"error": "请输入API Key"}), 400
    if not s["match_results"]:
        return jsonify({"error": "请先执行规则匹配"}), 400

    unmatched_items = []
    for r in s["match_results"]:
        if r["status"] in ("未匹配", "待匹配"):
            unmatched_items.append({"index": r["index"], "name": r["checklist_name"]})

    if not unmatched_items:
        return jsonify({"success": True, "matched_count": 0, "message": "所有项目已匹配，无需AI辅助"})

    scanned_names = []
    if s["scanned_files"]:
        scanned_names.extend([_os.path.basename(f) for f in s["scanned_files"]])
    if s.get("scanned_folders"):
        scanned_names.extend([_os.path.basename(f) for f in s["scanned_folders"]])

    if not scanned_names:
        return jsonify({"error": "没有扫描到的文件"}), 400

    scanned_names = list(dict.fromkeys(scanned_names))

    llm_started = time.perf_counter()
    try:
        result = llm_match(unmatched_items, scanned_names, config)
    except Exception as e:
        return jsonify({"error": f"LLM匹配失败: {str(e)}"}), 500

    llm_map = {}
    for item in result["results"]:
        if item.get("matched_name") and item.get("confidence", 0) >= 0.5:
            llm_map[item["index"]] = item

    updated_count = 0
    company_names = get_company_names_from_session(s)
    all_paths = (s.get("scanned_files") or []) + (s.get("scanned_folders") or [])
    root = s.get("scan_root", "")
    llm_duration_ms = round((time.perf_counter() - llm_started) * 1000)
    dev_logs = []
    checklist_by_index = {r["index"]: r for r in s["match_results"]}

    for item in result.get("results", []):
        confidence = item.get("confidence", 0)
        matched_name = item.get("matched_name", "")
        checklist = checklist_by_index.get(item.get("index"))
        if not matched_name or confidence < 0.5:
            dev_logs.append(_run_log(
                "l2", "match_rejected", "warning", root=root, checklist=checklist,
                strategy="llm_filename", confidence=confidence,
                duration_ms=llm_duration_ms,
                detail={"reason": "未返回文件" if not matched_name else "置信度低于阈值"},
            ))

    for r in s["match_results"]:
        if r["index"] in llm_map:
            llm_item = llm_map[r["index"]]
            matched_name = llm_item["matched_name"]
            matched_path, path_matches = _resolve_unique_path(all_paths, matched_name)
            if matched_path:
                company_coverage = {}
                if _os.path.isdir(matched_path):
                    try:
                        subdirs = [d for d in _os.listdir(matched_path) if _os.path.isdir(_os.path.join(matched_path, d))]
                        for cn in company_names:
                            if cn in subdirs:
                                if cn not in company_coverage:
                                    company_coverage[cn] = {"files": [], "folders": []}
                                company_coverage[cn]["folders"].append(matched_path)
                    except Exception:
                        pass

                    dirname = _os.path.basename(matched_path)
                    if dirname in company_names and dirname not in company_coverage:
                        company_coverage[dirname] = {"files": [], "folders": []}
                        company_coverage[dirname]["folders"].append(matched_path)
                else:
                    company = _find_company_in_path(matched_path, company_names)
                    if company:
                        if company not in company_coverage:
                            company_coverage[company] = {"files": [], "folders": []}
                        company_coverage[company]["files"].append(matched_path)
                    else:
                        company = _find_company_in_filename(_os.path.basename(matched_path), company_names)
                        if company:
                            if company not in company_coverage:
                                company_coverage[company] = {"files": [], "folders": []}
                            company_coverage[company]["files"].append(matched_path)

                r["status"] = "已获取"
                r["matched_files"] = [matched_path]
                r["matched_names"] = [matched_name]
                r["matched_types"] = ["文件夹" if _os.path.isdir(matched_path) else "文件"]
                r["match_count"] = len(r["matched_files"])
                r["llm_confidence"] = llm_item["confidence"]
                r["company_coverage"] = company_coverage
                updated_count += 1
                if not _os.path.isdir(matched_path):
                    dev_logs.append(_run_log(
                        "l2", "match_accepted", "success", path=matched_path, root=root,
                        checklist=r, strategy="llm_filename",
                        confidence=llm_item.get("confidence"), duration_ms=llm_duration_ms,
                        detail={"model_source": llm_item.get("source", "")},
                    ))
            else:
                dev_logs.append(_run_log(
                    "l2", "path_ambiguous" if len(path_matches) > 1 else "path_unresolved",
                    "error", root=root, checklist=r, strategy="llm_filename",
                    confidence=llm_item.get("confidence"), duration_ms=llm_duration_ms,
                    detail={"matched_name": matched_name, "candidate_count": len(path_matches)},
                ))

    matched_count = sum(1 for r in s["match_results"] if r["status"] in ("已获取", "部分获取"))
    partial_count = sum(1 for r in s["match_results"] if r["status"] == "部分获取")
    _store_dev_logs(s, dev_logs)
    return jsonify({
        "success": True,
        "matched_count": matched_count,
        "partial_count": partial_count,
        "total": len(s["match_results"]),
        "llm_matched": updated_count,
        "llm_results": result["results"],
        "match_results": s["match_results"],
        "usage": result.get("usage", {}),
        "root_path": s.get("scan_root", ""),
        "dev_logs": dev_logs,
    })


@app.route("/api/content-match", methods=["POST"])
def do_content_match():
    """L3 内容分类：读取未归属文件内容，并用 LLM 分类到全部清单项。"""
    s = _state()
    import os as _os
    config = request.get_json() or {}
    provider = config.get("provider", "")
    api_key = config.get("api_key", "")

    if not provider:
        return jsonify({"error": "请选择模型"}), 400
    if not api_key and provider != "ollama":
        return jsonify({"error": "请输入API Key"}), 400
    if not s["match_results"]:
        return jsonify({"error": "请先执行规则匹配"}), 400

    # 分类候选始终是全部清单项；已获取的清单项也允许追加更多文件。
    checklist_items = [
        {"index": r["index"], "name": r["checklist_name"]}
        for r in s["match_results"]
    ]

    # 收集已被匹配的文件路径，并逐文件记录当前清单项和公司归属。
    matched_paths = set()
    matched_checklist_by_path = {}
    company_assigned_paths = set()
    for r in s["match_results"]:
        for f in (r.get("matched_files") or []):
            normalized = _os.path.normpath(f)
            matched_paths.add(normalized)
            matched_checklist_by_path[normalized] = r["index"]
        for coverage in (r.get("company_coverage") or {}).values():
            for f in (coverage.get("files") or []) + (coverage.get("folders") or []):
                company_assigned_paths.add(_os.path.normpath(f))

    # 收集未匹配的文件
    all_paths = (s.get("scanned_files") or []) + (s.get("scanned_folders") or [])
    ignored_paths = set(s.get("content_ignored_files") or [])
    unmatched_paths = [
        p for p in all_paths
        if _os.path.normpath(p) not in matched_paths and _os.path.normpath(p) not in ignored_paths
    ]
    company_pending_paths = [
        p for p in (s.get("scanned_files") or [])
        if _os.path.normpath(p) in matched_paths
        and _os.path.normpath(p) not in company_assigned_paths
        and _os.path.normpath(p) not in ignored_paths
    ]
    candidate_paths = unmatched_paths + [p for p in company_pending_paths if p not in unmatched_paths]

    if not candidate_paths:
        return jsonify({"success": True, "matched_count": 0, "message": "没有未匹配或待补充公司归属的文件"})

    # 过滤出支持读取的文件（图片、PDF、docx、xlsx、文本等）
    SUPPORTED_EXT = {".txt", ".csv", ".log", ".json", ".xml", ".md", ".html", ".htm",
                     ".docx", ".xlsx", ".xls", ".pdf",
                     ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif", ".webp"}
    content_paths = [p for p in candidate_paths if _os.path.splitext(p)[1].lower() in SUPPORTED_EXT]

    if not content_paths:
        return jsonify({"success": True, "matched_count": 0, "message": "候选文件中没有可读取内容的文件类型"})

    # 读取 OCR 配置
    ocr_config = {"api_key": config.get("ocr_api_key", ""), "secret_key": config.get("ocr_secret_key", "")}

    # 读取缓存
    content_cache = s.get("file_content_cache", {})

    # 重置进度
    reset_progress()

    # 提取内容
    # 图片直接需要 OCR；PDF 会先读取文字层，文字不足时再回退 OCR。
    # 因此 PDF 也必须把 OCR 配置传入内容读取器。
    image_ext = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif", ".webp"}
    ocr_capable_ext = image_ext | {".pdf"}
    has_image = any(_os.path.splitext(p)[1].lower() in image_ext for p in content_paths)
    has_ocr_candidate = any(_os.path.splitext(p)[1].lower() in ocr_capable_ext for p in content_paths)
    ocr_configured = bool(ocr_config["api_key"] and ocr_config["secret_key"])
    if has_image and not ocr_configured:
        return jsonify({"error": "存在图片文件需要OCR识别，请输入百度OCR API Key和Secret Key"}), 400

    content_started = time.perf_counter()
    try:
        extraction_results, updated_cache = extract_contents(
            content_paths, ocr_config if has_ocr_candidate and ocr_configured else None, content_cache
        )
    except Exception as e:
        return jsonify({"error": f"文件内容提取失败: {str(e)}"}), 500

    # 写回缓存
    s["file_content_cache"] = updated_cache

    # 构建 scanned_items
    scanned_items = []
    extraction_count = 0
    ocr_count = 0
    path_by_file_id = {}
    for sequence, p in enumerate(content_paths, 1):
        result = extraction_results.get(p, {})
        content = result.get("content", "")
        ct = result.get("content_type", "")
        if content:
            file_id = f"f{sequence}"
            path_by_file_id[file_id] = p
            scanned_items.append({
                "file_id": file_id,
                "name": _os.path.basename(p),
                "relative_path": _relative_log_path(p, s.get("scan_root", "")),
                "fixed_checklist_index": matched_checklist_by_path.get(_os.path.normpath(p)),
                "content": content,
                "content_type": ct,
                "content_label": result.get("content_label", ""),
            })
            if content:
                extraction_count += 1
                if ct == "ocr":
                    ocr_count += 1

    if not scanned_items:
        return jsonify({"success": True, "matched_count": 0, "message": "未能从任何文件中提取到有效内容"})

    # 逐个未归属文件对照全部清单项进行内容分类。
    llm_started = time.perf_counter()
    try:
        companies = (s.get("checklist_template") or {}).get("companies", [])
        content_result = llm_classify_files_with_content(
            checklist_items, scanned_items, config, companies=companies
        )
    except Exception as e:
        return jsonify({"error": f"内容分类AI调用失败: {str(e)}"}), 500

    updated_count = 0
    company_names = get_company_names_from_session(s)
    root = s.get("scan_root", "")
    extraction_duration_ms = round((llm_started - content_started) * 1000)
    llm_duration_ms = round((time.perf_counter() - llm_started) * 1000)
    dev_logs = []
    checklist_by_index = {r["index"]: r for r in s["match_results"]}
    auto_threshold = float(config.get("content_auto_threshold", 0.75))
    company_auto_threshold = float(config.get("company_auto_threshold", 0.85))
    suggest_threshold = float(config.get("content_suggest_threshold", 0.45))
    suggestions = []
    unassigned_count = 0
    company_rechecked_count = 0

    for path in content_paths:
        extracted = extraction_results.get(path, {})
        content_type = extracted.get("content_type", "")
        error = extracted.get("error", "")
        dev_logs.append(_run_log(
            "extract", "extract_failed" if error else "extract_completed",
            "error" if error else "success", path=path, root=root,
            strategy="ocr" if content_type == "ocr" else "content_extract",
            duration_ms=extraction_duration_ms,
            detail={
                "content_type": content_type,
                "content_label": extracted.get("content_label", ""),
                "error": error[:160] if error else "",
                "content_length": len(extracted.get("content", "")),
            },
        ))

    for item in content_result.get("results", []):
        file_id = item.get("file_id", "")
        path = path_by_file_id.get(file_id)
        confidence = float(item.get("checklist_confidence", item.get("confidence", 0)) or 0)
        company_name = item.get("company_name")
        company_confidence = float(item.get("company_confidence", 0) or 0)
        raw_evidence = item.get("company_evidence") or []
        if isinstance(raw_evidence, str):
            raw_evidence = [raw_evidence]
        company_evidence = [str(value)[:120] for value in raw_evidence[:3]]
        detected_company_name = str(item.get("detected_company_name") or "")[:160]
        company_aliases = {}
        for company in companies:
            canonical = company.get("short_name") or company.get("full_name")
            if canonical:
                company_aliases[canonical] = canonical
                if company.get("full_name"):
                    company_aliases[company["full_name"]] = canonical
                if company.get("short_name"):
                    company_aliases[company["short_name"]] = canonical
        company_name = company_aliases.get(company_name)
        if company_name not in company_names:
            company_name = None
            company_confidence = 0
        company_is_certain = not companies or bool(
            company_name and company_evidence and company_confidence >= company_auto_threshold
        )
        checklist_index = item.get("checklist_index")
        try:
            checklist_index = int(checklist_index) if checklist_index is not None else None
        except (TypeError, ValueError):
            checklist_index = None
        fixed_checklist_index = matched_checklist_by_path.get(_os.path.normpath(path)) if path else None
        if fixed_checklist_index is not None:
            checklist_index = fixed_checklist_index
            confidence = 1.0
        checklist = checklist_by_index.get(checklist_index)
        reason = str(item.get("reason", ""))[:200]
        if fixed_checklist_index is not None and not reason:
            reason = "清单项已匹配，本轮仅补充识别公司归属"

        if not path:
            continue
        if checklist is None:
            unassigned_count += 1
            dev_logs.append(_run_log(
                "l3", "file_unassigned", "warning", path=path, root=root,
                strategy="llm_content", confidence=confidence, duration_ms=llm_duration_ms,
                detail={"reason": reason or "模型判断不属于任何清单项"},
            ))
            continue

        if confidence < auto_threshold or not company_is_certain:
            suggestion = {
                "file_id": file_id,
                "file_path": path,
                "file_name": _os.path.basename(path),
                "relative_path": _relative_log_path(path, root),
                "checklist_index": checklist_index,
                "checklist_name": checklist["checklist_name"],
                "confidence": confidence,
                "company_name": company_name,
                "company_confidence": company_confidence,
                "company_evidence": company_evidence,
                "detected_company_name": detected_company_name,
                "suggestion_type": "company_only" if fixed_checklist_index is not None else "item_and_company",
                "reason": reason,
            }
            if confidence >= suggest_threshold:
                suggestions.append(suggestion)
                event = "match_suggested"
                status = "warning"
            else:
                unassigned_count += 1
                event = "match_rejected"
                status = "warning"
            dev_logs.append(_run_log(
                "l3", event, status, path=path, root=root, checklist=checklist,
                strategy="llm_company_content" if fixed_checklist_index is not None else "llm_content",
                confidence=confidence, duration_ms=llm_duration_ms,
                detail={
                    "reason": reason or "置信度不足",
                    "company_name": company_name or "",
                    "company_confidence": company_confidence,
                    "company_evidence": company_evidence,
                    "detected_company_name": detected_company_name,
                },
            ))
            continue

        matched_files = checklist.setdefault("matched_files", [])
        if path not in matched_files:
            matched_files.append(path)
            checklist.setdefault("matched_names", []).append(_os.path.basename(path))
            checklist.setdefault("matched_types", []).append("文件夹" if _os.path.isdir(path) else "文件")
            updated_count += 1
        if company_name:
            coverage = checklist.setdefault("company_coverage", {})
            company_info = coverage.setdefault(company_name, {"files": [], "folders": []})
            if path not in company_info["files"]:
                company_info["files"].append(path)
                if fixed_checklist_index is not None:
                    company_rechecked_count += 1
        checklist["status"] = "已获取"
        checklist["match_count"] = len(matched_files)
        if fixed_checklist_index is None:
            checklist["llm_confidence"] = confidence
            checklist["matched_source"] = "content"
        dev_logs.append(_run_log(
            "l3", "match_accepted", "success", path=path, root=root,
            checklist=checklist,
            strategy="llm_company_content" if fixed_checklist_index is not None else "llm_content",
            confidence=confidence,
            duration_ms=llm_duration_ms, detail={
                "reason": reason,
                "company_name": company_name,
                "company_confidence": company_confidence,
                "company_evidence": company_evidence,
                "detected_company_name": detected_company_name,
            },
        ))

    s["content_suggestions"] = suggestions

    matched_count = sum(1 for r in s["match_results"] if r["status"] in ("已获取", "部分获取"))
    partial_count = sum(1 for r in s["match_results"] if r["status"] == "部分获取")
    _store_dev_logs(s, dev_logs)
    return jsonify({
        "success": True,
        "matched_count": matched_count,
        "partial_count": partial_count,
        "total": len(s["match_results"]),
        "llm_matched": updated_count,
        "llm_results": content_result["results"],
        "content_suggestions": suggestions,
        "match_results": s["match_results"],
        "usage": content_result.get("usage", {}),
        "root_path": s.get("scan_root", ""),
        "content_stats": {
            "files_processed": len(content_paths),
            "content_extracted": extraction_count,
            "ocr_processed": ocr_count,
            "auto_assigned": updated_count,
            "suggested": len(suggestions),
            "unassigned": unassigned_count,
            "company_rechecked": company_rechecked_count,
        },
        "dev_logs": dev_logs,
    })


@app.route("/api/content-match/progress", methods=["GET"])
def content_match_progress():
    """轮询 L3 内容提取进度"""
    return jsonify(get_progress())


# ====== 目录浏览 ======

@app.route("/api/browse-dirs", methods=["GET"])
def browse_dirs():
    """浏览指定路径下的子目录，用于文件夹选择弹窗"""
    path = request.args.get("path", "")
    path = urllib.parse.unquote(path) if path else ""

    if not path:
        if os.name == "nt":
            import string
            dirs = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.isdir(drive):
                    dirs.append({"name": drive, "path": drive})
            return jsonify({"current": "", "dirs": dirs})
        else:
            return jsonify({"current": "/", "dirs": [{"name": "home", "path": "/home"}]})

    if not os.path.isdir(path):
        return jsonify({"error": "路径无效"}), 400

    dirs = []
    try:
        for item in os.listdir(path):
            if item.startswith(".") or item.startswith("~"):
                continue
            full = os.path.join(path, item)
            if os.path.isdir(full):
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    mtime = 0
                dirs.append({"name": item, "path": full, "modified": mtime})
    except PermissionError:
        return jsonify({"error": "无权限访问该目录"}), 403

    dirs.sort(key=lambda x: x["name"].lower())
    return jsonify({"current": path, "dirs": dirs})


@app.route("/api/user-home", methods=["GET"])
def user_home():
    """返回当前用户的主目录路径"""
    home = os.path.expanduser("~")
    return jsonify({"home": home})


@app.route("/api/create-folder", methods=["POST"])
def create_folder():
    """在指定父目录下创建新文件夹"""
    data = request.get_json() or {}
    parent_path = data.get("parent_path", "").strip()
    folder_name = data.get("folder_name", "").strip()

    if not parent_path:
        return jsonify({"error": "未指定父目录"}), 400
    if not folder_name:
        return jsonify({"error": "未指定文件夹名称"}), 400
    folder_name = re.sub(r'[<>:"/\\|?*]', '_', folder_name)
    if not os.path.isdir(parent_path):
        return jsonify({"error": "父目录不存在"}), 400

    new_path = os.path.join(parent_path, folder_name)
    if os.path.exists(new_path):
        return jsonify({"error": "该文件夹已存在"}), 400

    try:
        os.makedirs(new_path)
        return jsonify({"success": True, "path": parent_path, "folder_name": folder_name})
    except Exception as e:
        return jsonify({"error": f"创建失败: {str(e)}"}), 500


# ====== 项目管理 API ======

@app.route("/api/project/list", methods=["GET"])
def api_project_list():
    """列出所有已保存的项目。"""
    try:
        projects = list_projects()
        return jsonify({"success": True, "projects": projects})
    except Exception as e:
        return jsonify({"error": f"获取项目列表失败: {str(e)}"}), 500


@app.route("/api/project/current", methods=["GET"])
def api_project_current():
    """获取当前活动项目信息。"""
    s = _state()
    active = s.get("active_project")
    if not active:
        return jsonify({"success": True, "active": None})
    return jsonify({"success": True, "active": active})


@app.route("/api/project/create", methods=["POST"])
def api_project_create():
    """创建新项目，保存当前状态。"""
    s = _state()
    data = request.get_json() or {}
    name = (data.get("project_name") or "").strip()
    if not name:
        return jsonify({"error": "项目名称不能为空"}), 400

    # 合并前端 company_status 到后端 checklist_template
    _merge_company_status(s, data)

    file_renames = data.get("file_renames", {})
    view_state = data.get("view_state", {})

    try:
        result = create_project(name, s, file_renames, view_state)
        s["active_project"] = {"slug": result["slug"], "name": name}
        return jsonify({
            "success": True,
            "slug": result["slug"],
            "file_path": result["file_path"],
            "message": f"项目「{name}」已保存",
        })
    except Exception as e:
        return jsonify({"error": f"创建项目失败: {str(e)}"}), 500


def _merge_company_status(session_state, request_data):
    """将前端传来的 company_status 合并到后端 session 的 checklist_template 中。

    request_data 可能包含:
    - view_state.preview_items: [{row_index, company_status}, ...]
    - 前端每次保存时都会发送当前 previewItems 的 company_status
    """
    view_state = request_data.get("view_state", {}) or {}
    preview_items = view_state.get("preview_items", [])
    if not preview_items:
        return
    tpl = session_state.get("checklist_template")
    if not tpl or not tpl.get("items"):
        return
    # 建立 row_index -> item 的索引
    item_map = {it["row_index"]: it for it in tpl["items"]}
    for pi in preview_items:
        ri = pi.get("row_index")
        cs = pi.get("company_status", {})
        if ri in item_map and cs:
            # 只合并非空状态值
            item_map[ri]["company_status"] = cs


@app.route("/api/project/save", methods=["POST"])
def api_project_save():
    """保存当前状态到活动项目。"""
    s = _state()
    data = request.get_json() or {}
    active = s.get("active_project")

    # 合并前端 company_status 到后端 checklist_template
    _merge_company_status(s, data)

    if not active:
        # 没有活动项目，需要先创建
        project_name = data.get("project_name", "").strip()
        if not project_name:
            return jsonify({"error": "请先创建项目或提供项目名称"}), 400
        file_renames = data.get("file_renames", {})
        view_state = data.get("view_state", {})
        try:
            result = create_project(project_name, s, file_renames, view_state)
            s["active_project"] = {"slug": result["slug"], "name": project_name}
            return jsonify({
                "success": True,
                "slug": result["slug"],
                "message": f"项目「{project_name}」已保存",
            })
        except Exception as e:
            return jsonify({"error": f"保存失败: {str(e)}"}), 500

    slug = active["slug"]
    file_renames = data.get("file_renames", {})
    view_state = data.get("view_state", {})

    try:
        filepath = save_project(slug, s, file_renames, view_state)
        return jsonify({
            "success": True,
            "slug": slug,
            "file_path": filepath,
            "message": "项目已保存",
        })
    except Exception as e:
        return jsonify({"error": f"保存失败: {str(e)}"}), 500


@app.route("/api/project/load", methods=["POST"])
def api_project_load():
    """加载已保存的项目，恢复到当前会话。"""
    s = _state()
    data = request.get_json() or {}
    slug = (data.get("project_slug") or "").strip()
    if not slug:
        return jsonify({"error": "未指定项目"}), 400

    try:
        loaded = load_project(slug)
    except Exception as e:
        return jsonify({"error": f"加载项目失败: {str(e)}"}), 500

    if loaded is None:
        return jsonify({"error": "项目不存在或文件损坏"}), 404

    # 恢复后端状态
    restored_state = loaded["state"]
    for key, value in restored_state.items():
        s[key] = value

    return jsonify({
        "success": True,
        "project_name": loaded["project_name"],
        "created_at": loaded.get("created_at", ""),
        "updated_at": loaded.get("updated_at", ""),
        "file_renames": loaded.get("file_renames", {}),
        "view_state": loaded.get("view_state", {}),
        "checklist_template": s.get("checklist_template"),
        "match_results": s.get("match_results"),
        "scanned_files": s.get("scanned_files"),
        "scanned_folders": s.get("scanned_folders"),
        "scan_root": s.get("scan_root"),
        "scan_needs_match": s.get("scan_needs_match", False),
        "last_scan_diff": s.get("last_scan_diff"),
        "checklist": s.get("checklist"),
        "checklist_file_path": s.get("checklist_file_path"),
        "dev_logs": s.get("dev_logs") or [],
        "message": f"已加载项目「{loaded['project_name']}」",
    })


@app.route("/api/project/delete", methods=["DELETE"])
def api_project_delete():
    """删除指定项目。"""
    s = _state()
    data = request.get_json() or {}
    slug = (data.get("project_slug") or "").strip()
    if not slug:
        return jsonify({"error": "未指定项目"}), 400

    try:
        deleted = delete_project(slug)
    except Exception as e:
        return jsonify({"error": f"删除失败: {str(e)}"}), 500

    if not deleted:
        return jsonify({"error": "项目不存在"}), 404

    # 如果删除的是当前活动项目，清除活动状态
    active = s.get("active_project")
    if active and active.get("slug") == slug:
        s["active_project"] = None

    return jsonify({"success": True, "message": "项目已删除"})


# ====== 启动 ======

if __name__ == "__main__":
    import atexit
    import threading
    import webbrowser
    atexit.register(session_store.shutdown)

    # 是否为 PyInstaller 打包运行
    is_frozen = getattr(sys, "frozen", False)

    if is_frozen:
        # 打包后直接启动浏览器，不加 debug 模式（避免双进程）
        def _open_browser():
            webbrowser.open("http://127.0.0.1:5001")
        threading.Timer(1.5, _open_browser).start()
        # 打包后 stdout 可能是 GBK 编码，避免 emoji 报错
        try:
            print("\U0001f680 PBC审计工具已启动，浏览器将自动打开...")
        except UnicodeEncodeError:
            print("PBC审计工具已启动，浏览器将自动打开...")
        app.run(debug=False, port=5001)
    else:
        # 开发环境：只在 reloader 子进程中打开浏览器，避免 debug 模式弹出两个标签页
        if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            def _open_browser():
                webbrowser.open("http://127.0.0.1:5001")
            threading.Timer(1.0, _open_browser).start()
        print("\U0001f680 启动中，稍后将自动打开浏览器...")
        app.run(debug=True, port=5001)
