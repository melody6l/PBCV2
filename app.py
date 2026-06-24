"""审计文件匹配工具 - Flask主应用"""

import os
import re
import shutil
import json
import urllib.parse
from flask import Flask, request, jsonify, render_template, send_file, make_response
from matcher import match_files, _find_company_in_path, _find_company_in_filename
from excel_handler import normalize_item_name, export_checklist_two_sheets, build_browse_items
from llm_matcher import llm_match
from template_handler import (
    read_template, generate_checklist, read_user_checklist, update_cell_status,
    add_row_to_checklist, edit_row_in_checklist, delete_row_from_checklist,
)

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = "uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# 全局状态（单用户模式）
state = {
    "checklist": None,       # 清单数据
    "scanned_files": None,   # 扫描到的文件列表
    "scanned_folders": None, # 扫描到的文件夹列表
    "match_results": None,   # 匹配结果
    "scan_root": None,       # 当前扫描根目录
    "new_items": [],
    "existing_items": [],
    "previous_scanned_files": [],
    "previous_scanned_folders": [],
    "checklist_template": None,    # 从模板生成的清单数据
    "checklist_file_path": None,   # 当前使用的清单Excel路径
}


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
            "index": i,
            "checklist_name": item.get("name", ""),
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


def reset_state_values():
    state.update({
        "checklist": None,
        "scanned_files": None,
        "scanned_folders": None,
        "match_results": None,
        "scan_root": None,
        "new_items": [],
        "existing_items": [],
        "previous_scanned_files": [],
        "previous_scanned_folders": [],
        "checklist_template": None,
        "checklist_file_path": None,
    })


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan-folder", methods=["POST"])
def scan_folder():
    """扫描指定文件夹"""
    data = request.get_json()
    folder_path = data.get("folder_path", "")
    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({"error": "文件夹路径无效"}), 400

    prev_files = state.get("scanned_files")
    prev_folders = state.get("scanned_folders")
    if prev_files is None and prev_folders is None:
        prev_files = state.get("previous_scanned_files") or None
        prev_folders = state.get("previous_scanned_folders") or None
    scanned_files, scanned_folders = scan_all(folder_path)
    diff = calculate_diff(prev_files, scanned_files, prev_folders, scanned_folders)

    state["scanned_files"] = scanned_files
    state["scanned_folders"] = scanned_folders
    # 记录扫描根路径
    state["scan_root"] = folder_path
    # 自动执行匹配
    if state["checklist"]:
        prev_results = state.get("match_results") if state["checklist"].get("has_previous_results") else None
        # 获取公司列表
        company_names = []
        if state.get("checklist_template") and state["checklist_template"].get("companies"):
            company_names = [c.get("short_name") or c.get("full_name") for c in state["checklist_template"]["companies"]]

        results = match_files(
            state["checklist"]["items"],
            scanned_files,
            scanned_folders,
            prev_results=prev_results,
            company_names=company_names,
        )
        state["match_results"] = results
        matched_count, partial_count = result_counts(results)
        return jsonify({
            "success": True,
            "scanned_count": len(scanned_files) + len(scanned_folders),
            "diff": diff,
            "checklist_diff": {
                "new_count": len(state.get("new_items", [])),
                "existing_count": len(state.get("existing_items", [])),
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
    data = request.get_json()
    incremental = data.get("incremental", False)

    if not state["checklist"]:
        return jsonify({"error": "请先上传清单文件"}), 400
    if not state["scanned_files"] and not state.get("scanned_folders"):
        return jsonify({"error": "请先扫描目标文件夹"}), 400

    prev_results = state.get("match_results") if incremental and state["checklist"].get("has_previous_results") else None
    # 获取公司列表
    company_names = []
    if state.get("checklist_template") and state["checklist_template"].get("companies"):
        company_names = [c.get("short_name") or c.get("full_name") for c in state["checklist_template"]["companies"]]

    results = match_files(
        state["checklist"]["items"],
        state["scanned_files"],
        state.get("scanned_folders", []),
        prev_results=prev_results,
        company_names=company_names,
    )
    state["match_results"] = results
    matched_count, partial_count = result_counts(results)
    return jsonify({
        "success": True,
        "results": results,
        "matched_count": matched_count,
        "partial_count": partial_count,
        "total": len(results),
        "root_path": state.get("scan_root", ""),
    })




@app.route("/api/reset-state", methods=["POST"])
def reset_state():
    """重置所有状态，重新开始。"""
    reset_state_values()
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
    data = request.get_json()
    subjects = data.get("subjects", [])
    company_full = data.get("company_full_name", "")
    company_short = data.get("company_short_name", "")
    if not subjects:
        return jsonify({"error": "请至少选择一个科目"}), 400

    try:
        file_path = generate_checklist(subjects, company_full, company_short)
        state["checklist_file_path"] = file_path

        # 读取生成的清单
        tpl_data = read_user_checklist(file_path)
        state["checklist_template"] = tpl_data

        # 同时构建兼容匹配流程的 checklist 数据
        items_for_match = []
        for it in tpl_data["items"]:
            name = it.get("demand_name") or it.get("pbc_name") or ""
            if name:
                items_for_match.append({
                    "name": name,
                    "source_key": normalize_item_name(name),
                    "row_uid": "",
                })
        state["checklist"] = {
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
    """下载生成的PBC需求清单"""
    file_path = state.get("checklist_file_path")
    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "请先生成PBC需求清单"}), 400
    return send_file(file_path, as_attachment=True, download_name="PBC需求清单_待填写.xlsx")


@app.route("/api/upload-checklist-v2", methods=["POST"])
def upload_checklist_v2():
    """上传用户填写后的PBC需求清单，解析为预览数据"""
    if "file" not in request.files:
        return jsonify({"error": "未提供文件"}), 400
    file = request.files["file"]
    if not file.filename.endswith(".xlsx"):
        return jsonify({"error": "仅支持.xlsx格式文件"}), 400

    save_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
    file.save(save_path)
    state["checklist_file_path"] = save_path

    try:
        tpl_data = read_user_checklist(save_path)
        state["checklist_template"] = tpl_data

        # 同时构建兼容旧流程的匹配数据
        items = []
        for it in tpl_data["items"]:
            name = it.get("demand_name") or it.get("pbc_name") or ""
            if name:
                items.append({
                    "name": name,
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
        state["checklist"] = checklist

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


@app.route("/api/update-cell-status", methods=["POST"])
def api_update_cell_status():
    """更新某个单元格的获取状态"""
    data = request.get_json()
    row_index = data.get("row_index")
    company_name = data.get("company_name")
    status = data.get("status", "Y")
    file_path = state.get("checklist_file_path")

    if not file_path:
        return jsonify({"error": "请先上传或生成清单"}), 400
    if not row_index or not company_name:
        return jsonify({"error": "参数不完整"}), 400

    try:
        update_cell_status(file_path, row_index, company_name, status)
        # 同步更新内存中的数据
        if state["checklist_template"]:
            for item in state["checklist_template"]["items"]:
                if item["row_index"] == row_index:
                    item["company_status"][company_name] = status
                    break
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/add-row", methods=["POST"])
def api_add_row():
    """在核对清单中新增一行 PBC 需求"""
    data = request.get_json()
    subject = (data.get("subject") or "").strip()
    pbc_name = (data.get("pbc_name") or "").strip()
    demand_name = (data.get("demand_name") or "").strip() or pbc_name
    # 可选：插入位置（上方/下方的参照 row_index）
    position = data.get("position")         # "top" | "bottom" | None(末尾)
    ref_row_index = data.get("ref_row_index")  # 参照行

    file_path = state.get("checklist_file_path")
    if not file_path:
        return jsonify({"error": "请先上传或生成清单"}), 400
    if not subject or not pbc_name:
        return jsonify({"error": "科目和PBC名称不能为空"}), 400

    try:
        new_row_index = add_row_to_checklist(
            file_path, subject, pbc_name, demand_name,
            position=position, ref_row_index=ref_row_index
        )
    except Exception as e:
        return jsonify({"error": f"写入Excel失败: {str(e)}"}), 500

    # 构造新条目
    company_names = []
    if state.get("checklist_template"):
        company_names = state["checklist_template"].get("company_names", [])

    new_item = {
        "row_index": new_row_index,
        "seq": new_row_index,  # 前端会重新编号
        "subject": subject,
        "pbc_name": pbc_name,
        "demand_name": demand_name,
        "company_status": {cn: "N" for cn in company_names},
        "_custom": True,  # 标记为自定义行
    }

    # 更新内存 state
    if state.get("checklist_template"):
        state["checklist_template"]["items"].append(new_item)
    if state.get("checklist"):
        state["checklist"]["items"].append({
            "name": demand_name,
            "source_key": normalize_item_name(demand_name),
            "row_uid": f"custom_{new_row_index}",
        })

    return jsonify({"success": True, "item": new_item})


@app.route("/api/edit-row", methods=["POST"])
def api_edit_row():
    """编辑核对清单中某行的科目/PBC/需求资料"""
    data = request.get_json()
    row_index = data.get("row_index")
    field = data.get("field")       # "subject" | "pbc_name" | "demand_name"
    value = (data.get("value") or "").strip()

    if not row_index or not field:
        return jsonify({"error": "缺少 row_index 或 field"}), 400
    if field not in ("subject", "pbc_name", "demand_name"):
        return jsonify({"error": "无效的字段名"}), 400

    file_path = state.get("checklist_file_path")
    if not file_path:
        return jsonify({"error": "请先上传或生成清单"}), 400

    try:
        edit_row_in_checklist(file_path, row_index, field, value)
    except Exception as e:
        return jsonify({"error": f"编辑Excel失败: {str(e)}"}), 500

    # 更新内存 state
    if state.get("checklist_template"):
        for item in state["checklist_template"]["items"]:
            if item.get("row_index") == row_index:
                item[field] = value
                # 如果编辑了 demand_name，同步更新 checklist.items
                if field == "demand_name" and state.get("checklist"):
                    for ci in state["checklist"]["items"]:
                        if ci.get("row_uid") == f"custom_{row_index}" or \
                           ci.get("row_uid") == str(row_index):
                            ci["name"] = value
                            ci["source_key"] = normalize_item_name(value)
                break

    return jsonify({"success": True})


@app.route("/api/delete-row", methods=["POST"])
def api_delete_row():
    """删除核对清单中的一行（逻辑删除：在隐藏列标记 _deleted）"""
    data = request.get_json()
    row_index = data.get("row_index")

    if not row_index:
        return jsonify({"error": "缺少 row_index"}), 400

    file_path = state.get("checklist_file_path")
    if not file_path:
        return jsonify({"error": "请先上传或生成清单"}), 400

    try:
        delete_row_from_checklist(file_path, row_index)
    except Exception as e:
        return jsonify({"error": f"删除失败: {str(e)}"}), 500

    # 更新内存 state
    if state.get("checklist_template"):
        state["checklist_template"]["items"] = [
            it for it in state["checklist_template"]["items"]
            if it.get("row_index") != row_index
        ]
    if state.get("checklist"):
        state["checklist"]["items"] = [
            ci for ci in state["checklist"]["items"]
            if ci.get("row_uid") not in (f"custom_{row_index}", str(row_index))
        ]

    return jsonify({"success": True})


@app.route("/api/export-checklist", methods=["GET", "POST"])
def export_checklist():
    """导出包含矩阵视图和清单视图两个sheet的Excel

    POST 时接收前端 company_status 覆盖后端状态，确保导出使用核对后的结果
    """
    tpl_data = state.get("checklist_template")
    if not tpl_data:
        return jsonify({"error": "无清单数据可导出"}), 400

    items = tpl_data.get("items", [])
    company_names = tpl_data.get("company_names", [])
    match_results = state.get("match_results", [])

    if not items:
        return jsonify({"error": "清单项为空"}), 400

    # POST 方式：用前端发来的 company_status 覆盖后端状态
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
            state.get("scanned_files") or [],
            state.get("scanned_folders") or [],
            state.get("scan_root") or "",
        )
        return send_file(output_path, as_attachment=True, download_name="PBC需求清单.xlsx")
    except Exception as e:
        return jsonify({"error": f"导出失败: {str(e)}"}), 500


def get_matched_paths():
    """收集所有已被匹配引用的路径集合"""
    matched = set()
    if state["match_results"]:
        for r in state["match_results"]:
            for p in r["matched_files"]:
                matched.add(p)
    return matched


def build_browse_view_items():
    """构建以扫描资料为中心的动态层级列表。"""
    return build_browse_items(
        state.get("scanned_files") or [],
        state.get("scanned_folders") or [],
        state.get("scan_root") or "",
        state.get("match_results") or [],
    )


@app.route("/api/browse-view-data", methods=["GET"])
def browse_view_data():
    """返回所有扫描文件的资料浏览数据。"""
    items, folder_levels = build_browse_view_items()
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
    path = request.args.get("path", "")
    path = urllib.parse.unquote(path)
    if not path or not os.path.isdir(path):
        return jsonify({"error": "路径无效"}), 400

    matched_paths = get_matched_paths()
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
    # 排序：文件夹在前，文件在后
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return jsonify({"items": items})


@app.route("/api/manual-match", methods=["POST"])
def manual_match():
    """手动将文件/文件夹分配到清单中某一行"""
    data = request.get_json()
    file_path = data.get("file_path")
    index = data.get("index")  # 1-based 清单行序号

    if not state["match_results"]:
        return jsonify({"error": "尚无匹配结果"}), 400

    matched_result = None
    for r in state["match_results"]:
        if r["index"] == index:
            if file_path not in r["matched_files"]:
                r["status"] = "已获取"
                r["matched_files"].append(file_path)
                r["matched_names"].append(os.path.basename(file_path))
                r["matched_types"].append("文件夹" if os.path.isdir(file_path) else "文件")
                r["match_count"] = len(r["matched_files"])
            matched_result = r
            break

    if matched_result is None:
        return jsonify({"error": "未找到指定序号"}), 400

    matched_count = sum(1 for r in state["match_results"] if r["status"] in ("已获取", "部分获取"))
    partial_count = sum(1 for r in state["match_results"] if r["status"] == "部分获取")
    return jsonify({
        "success": True,
        "matched_count": matched_count,
        "partial_count": partial_count,
        "total": len(state["match_results"]),
        "match_results": matched_result,
    })


@app.route("/api/assign-company", methods=["POST"])
def assign_company():
    """人工确认公司归属：为已匹配清单项指定公司列表"""
    data = request.get_json()
    index = data.get("index")
    company_names = data.get("company_names") or []

    if not state["match_results"]:
        return jsonify({"error": "尚无匹配结果"}), 400
    if not index:
        return jsonify({"error": "缺少序号参数"}), 400

    for r in state["match_results"]:
        if r["index"] == index:
            if not company_names:
                # 取消全部分配，但保留已获取状态（不清空文件）
                r["company_coverage"] = {}
            else:
                # 重新设置公司归属
                new_coverage = {}
                for c in company_names:
                    new_coverage[c] = {"files": [], "folders": []}
                    # 把已有匹配文件归类到对应公司
                    for fp in r.get("matched_files", []):
                        if os.path.isdir(fp):
                            new_coverage[c]["folders"].append(fp)
                        else:
                            new_coverage[c]["files"].append(fp)
                r["company_coverage"] = new_coverage

            matched_count = sum(1 for x in state["match_results"] if x["status"] in ("已获取", "部分获取"))
            partial_count = sum(1 for x in state["match_results"] if x["status"] == "部分获取")
            return jsonify({
                "success": True,
                "index": index,
                "company_coverage": r["company_coverage"],
                "matched_count": matched_count,
                "partial_count": partial_count,
                "total": len(state["match_results"]),
            })

    return jsonify({"error": "未找到指定序号"}), 400


@app.route("/api/organize-files", methods=["POST"])
def organize_files():
    """整理已获取的文件：按 科目/需求资料 层级建文件夹，用户指定目标路径"""
    match_results = state.get("match_results", [])
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

    checklist_template = state.get("checklist_template", {})
    items = checklist_template.get("items", [])
    company_names = checklist_template.get("company_names", [])

    organized = []
    errors = []
    copied_paths = set()  # 去重：同一文件只复制一次

    for result in match_results:
        if result["status"] not in ("已获取", "部分获取"):
            continue

        # result["index"] 是 1-based 序号，与 items 数组顺序一致
        idx = result["index"]
        if idx < 1 or idx > len(items):
            continue
        item = items[idx - 1]
        if not item:
            continue

        subject = item.get("subject", "")
        demand_name = item.get("demand_name", "") or item.get("pbc_name", "")
        if not subject or not demand_name:
            continue

        # 目标：{target_path}/{subject}/{demand_name}/
        subject_dir = re.sub(r'[<>:"/\\|?*]', '_', subject)
        demand_dir = re.sub(r'[<>:"/\\|?*]', '_', demand_name)
        base_dir = os.path.join(target_path, subject_dir, demand_dir)

        company_coverage = result.get("company_coverage", {})
        matched_files = result.get("matched_files", []) or []
        matched_names = result.get("matched_names", []) or []

        # 有公司覆盖信息
        if company_coverage and any(v for v in company_coverage.values()):
            for cName, coverage in company_coverage.items():
                all_paths = (coverage.get("files", []) or []) + (coverage.get("folders", []) or [])
                if not all_paths:
                    continue

                # 仅当文件数 > 1 时建公司子文件夹，否则直接放需求资料文件夹
                use_subdir = len(all_paths) > 1

                for src_path in all_paths:
                    if src_path in copied_paths or not os.path.exists(src_path):
                        continue
                    copied_paths.add(src_path)

                    src_name = file_renames.get(src_path, os.path.basename(src_path))

                    # 检查文件名是否含公司简称
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

        # 无公司覆盖信息，但有匹配文件
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


@app.route("/api/open", methods=["GET"])
def open_file():
    """通过Flask中转打开本地文件或浏览文件夹"""
    path = request.args.get("path", "")
    path = urllib.parse.unquote(path)
    if not path or not os.path.exists(path):
        return jsonify({"error": "路径不存在"}), 404

    if os.path.isdir(path):
        # 文件夹：返回内容列表页面
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
        # 文件：直接提供下载/打开
        directory = os.path.dirname(path)
        filename = os.path.basename(path)
        return send_file(path, as_attachment=False)




@app.route("/api/llm-match", methods=["POST"])
def do_llm_match():
    """使用LLM辅助匹配未识别的清单项"""
    import os as _os
    config = request.get_json()
    provider = config.get("provider", "")
    api_key = config.get("api_key", "")

    if not provider:
        return jsonify({"error": "请选择模型"}), 400
    if not api_key and provider != "ollama":
        return jsonify({"error": "请输入API Key"}), 400
    if not state["match_results"]:
        return jsonify({"error": "请先执行规则匹配"}), 400

    # 收集未匹配项
    unmatched_items = []
    for r in state["match_results"]:
        if r["status"] in ("未匹配", "待匹配"):
            unmatched_items.append({"index": r["index"], "name": r["checklist_name"]})

    if not unmatched_items:
        return jsonify({"success": True, "matched_count": 0, "message": "所有项目已匹配，无需AI辅助"})

    # 收集所有扫描到的文件名
    scanned_names = []
    if state["scanned_files"]:
        scanned_names.extend([_os.path.basename(f) for f in state["scanned_files"]])
    if state.get("scanned_folders"):
        scanned_names.extend([_os.path.basename(f) for f in state["scanned_folders"]])

    if not scanned_names:
        return jsonify({"error": "没有扫描到的文件"}), 400

    # 去重
    scanned_names = list(dict.fromkeys(scanned_names))

    try:
        result = llm_match(unmatched_items, scanned_names, config)
    except Exception as e:
        return jsonify({"error": f"LLM匹配失败: {str(e)}"}), 500

    # 将LLM结果更新到匹配结果中
    llm_map = {}
    for item in result["results"]:
        if item.get("matched_name") and item.get("confidence", 0) >= 0.5:
            llm_map[item["index"]] = item

    updated_count = 0
    # 获取公司名列表
    company_names = []
    if state.get("checklist_template") and state["checklist_template"].get("companies"):
        company_names = [c.get("short_name") or c.get("full_name") for c in state["checklist_template"]["companies"]]

    for r in state["match_results"]:
        if r["index"] in llm_map:
            llm_item = llm_map[r["index"]]
            matched_name = llm_item["matched_name"]
            # 在扫描列表中找到对应路径
            matched_path = None
            all_paths = (state.get("scanned_files") or []) + (state.get("scanned_folders") or [])
            for p in all_paths:
                if _os.path.basename(p) == matched_name:
                    matched_path = p
                    break
            if matched_path:
                # 检测公司归属
                company_coverage = {}
                if _os.path.isdir(matched_path):
                    # Pattern A：检查文件夹内是否有公司子文件夹
                    try:
                        subdirs = [d for d in _os.listdir(matched_path) if _os.path.isdir(_os.path.join(matched_path, d))]
                        for cn in company_names:
                            if cn in subdirs:
                                if cn not in company_coverage:
                                    company_coverage[cn] = {"files": [], "folders": []}
                                company_coverage[cn]["folders"].append(matched_path)
                    except Exception:
                        pass

                    # 检查文件夹本身是否是公司文件夹
                    dirname = _os.path.basename(matched_path)
                    if dirname in company_names and dirname not in company_coverage:
                        company_coverage[dirname] = {"files": [], "folders": []}
                        company_coverage[dirname]["folders"].append(matched_path)
                else:
                    # Pattern B：检查文件名或父目录中的公司名
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

    matched_count = sum(1 for r in state["match_results"] if r["status"] in ("已获取", "部分获取"))
    partial_count = sum(1 for r in state["match_results"] if r["status"] == "部分获取")
    return jsonify({
        "success": True,
        "matched_count": matched_count,
        "partial_count": partial_count,
        "total": len(state["match_results"]),
        "llm_matched": updated_count,
        "llm_results": result["results"],
        "match_results": state["match_results"],
        "usage": result.get("usage", {}),
        "root_path": state.get("scan_root", ""),
    })


@app.route("/api/browse-dirs", methods=["GET"])
def browse_dirs():
    """浏览指定路径下的子目录，用于文件夹选择弹窗"""
    path = request.args.get("path", "")
    path = urllib.parse.unquote(path) if path else ""

    if not path:
        # 返回磁盘根目录
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
    # 过滤非法字符
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




if __name__ == "__main__":
    app.run(debug=True, port=5001)
