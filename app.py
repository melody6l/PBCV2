"""审计文件匹配工具 - Flask主应用"""

import os
import re
import shutil
import json
import uuid
import urllib.parse
from flask import Flask, request, jsonify, render_template, send_file, make_response
from matcher import match_files, _find_company_in_path, _find_company_in_filename
from excel_handler import normalize_item_name, export_checklist_two_sheets, build_browse_items
from llm_matcher import llm_match
from template_handler import (
    read_template, generate_checklist, read_user_checklist,
    generate_checklist_from_memory,
)
from project_manager import (
    list_projects, load_project, save_project, create_project, delete_project,
)
from session_manager import get_session_store, create_fresh_state

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = "uploads"
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


# ====== 扫描 & 匹配 ======

@app.route("/api/scan-folder", methods=["POST"])
def scan_folder():
    """扫描指定文件夹"""
    s = _state()
    data = request.get_json()
    folder_path = data.get("folder_path", "")
    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({"error": "文件夹路径无效"}), 400

    prev_files = s.get("scanned_files")
    prev_folders = s.get("scanned_folders")
    if prev_files is None and prev_folders is None:
        prev_files = s.get("previous_scanned_files") or None
        prev_folders = s.get("previous_scanned_folders") or None
    scanned_files, scanned_folders = scan_all(folder_path)
    diff = calculate_diff(prev_files, scanned_files, prev_folders, scanned_folders)

    s["scanned_files"] = scanned_files
    s["scanned_folders"] = scanned_folders
    s["scan_root"] = folder_path

    # 自动执行匹配
    if s["checklist"]:
        prev_results = s.get("match_results") if s["checklist"].get("has_previous_results") else None
        # 获取公司列表
        company_names = []
        if s.get("checklist_template") and s["checklist_template"].get("companies"):
            company_names = [c.get("short_name") or c.get("full_name") for c in s["checklist_template"]["companies"]]

        results = match_files(
            s["checklist"]["items"],
            scanned_files,
            scanned_folders,
            prev_results=prev_results,
            company_names=company_names,
            merge_mode=True,  # 增量合并模式：已匹配项也检查新文件
        )
        s["match_results"] = results
        matched_count, partial_count = result_counts(results)
        return jsonify({
            "success": True,
            "scanned_count": len(scanned_files) + len(scanned_folders),
            "diff": diff,
            "checklist_diff": {
                "new_count": len(s.get("new_items", [])),
                "existing_count": len(s.get("existing_items", [])),
            },
            "results": results,
            "matched_count": matched_count,
            "partial_count": partial_count,
            "total": len(results),
            "root_path": folder_path,
        })
    else:
        return jsonify({
            "success": True,
            "scanned_count": len(scanned_files) + len(scanned_folders),
            "diff": diff,
            "results": [],
            "message": "请先上传清单文件再执行匹配",
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

    prev_results = s.get("match_results") if incremental and s["checklist"].get("has_previous_results") else None
    # 获取公司列表
    company_names = []
    if s.get("checklist_template") and s["checklist_template"].get("companies"):
        company_names = [c.get("short_name") or c.get("full_name") for c in s["checklist_template"]["companies"]]

    results = match_files(
        s["checklist"]["items"],
        s["scanned_files"],
        s.get("scanned_folders", []),
        prev_results=prev_results,
        company_names=company_names,
        merge_mode=True,
    )
    s["match_results"] = results
    matched_count, partial_count = result_counts(results)
    return jsonify({
        "success": True,
        "results": results,
        "matched_count": matched_count,
        "partial_count": partial_count,
        "total": len(results),
        "root_path": s.get("scan_root", ""),
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
                items_for_match.append({
                    "name": name,
                    "pbc_name": it.get("pbc_name", ""),
                    "source_key": normalize_item_name(name),
                    "row_uid": "",
                })
        s["checklist"] = {
            "headers": tpl_data.get("headers", []),
            "data": [],
            "name_col_index": 3,
            "items": items_for_match,
            "has_previous_results": False,
        }

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
                items.append({
                    "name": name,
                    "pbc_name": it.get("pbc_name", ""),
                    "source_key": normalize_item_name(name),
                    "row_uid": "",
                })

        checklist = {
            "headers": tpl_data.get("headers", []),
            "data": [],
            "name_col_index": 3,
            "items": items,
            "has_previous_results": False,
        }
        s["checklist"] = checklist

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
    return jsonify({
        "success": True,
        "items": items,
        "folder_levels": folder_levels,
        "total": len(items),
        "matched_count": sum(1 for item in items if item["is_matched"]),
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

    if not s["match_results"]:
        return jsonify({"error": "尚无匹配结果"}), 400
    if index is None:
        return jsonify({"error": "缺少序号参数"}), 400

    for r in s["match_results"]:
        if r["index"] == index:
            if not company_names:
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

    try:
        result = llm_match(unmatched_items, scanned_names, config)
    except Exception as e:
        return jsonify({"error": f"LLM匹配失败: {str(e)}"}), 500

    llm_map = {}
    for item in result["results"]:
        if item.get("matched_name") and item.get("confidence", 0) >= 0.5:
            llm_map[item["index"]] = item

    updated_count = 0
    company_names = []
    if s.get("checklist_template") and s["checklist_template"].get("companies"):
        company_names = [c.get("short_name") or c.get("full_name") for c in s["checklist_template"]["companies"]]

    for r in s["match_results"]:
        if r["index"] in llm_map:
            llm_item = llm_map[r["index"]]
            matched_name = llm_item["matched_name"]
            matched_path = None
            all_paths = (s.get("scanned_files") or []) + (s.get("scanned_folders") or [])
            for p in all_paths:
                if _os.path.basename(p) == matched_name:
                    matched_path = p
                    break
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

    matched_count = sum(1 for r in s["match_results"] if r["status"] in ("已获取", "部分获取"))
    partial_count = sum(1 for r in s["match_results"] if r["status"] == "部分获取")
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
    })


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
        "checklist": s.get("checklist"),
        "checklist_file_path": s.get("checklist_file_path"),
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
    import webbrowser
    import threading
    atexit.register(session_store.shutdown)

    # 只在 reloader 子进程中打开浏览器，避免 debug 模式弹出两个标签页
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        def _open_browser():
            webbrowser.open("http://127.0.0.1:5001")
        threading.Timer(1.0, _open_browser).start()

    print("🚀 启动中，稍后将自动打开浏览器...")
    app.run(debug=True, port=5001)
