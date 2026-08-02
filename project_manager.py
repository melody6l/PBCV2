"""项目管理模块 — 项目文件的保存、加载、列表、删除。

项目文件为 JSON 格式，存放在 projects/ 目录下，完整序列化运行时状态。
用户通过项目名称（自动转为 slug）进行识别，无需接触 JSON 文件。
"""

import json
import os
import re
import shutil
from datetime import datetime
from path_utils import get_data_dir


def _get_projects_dir():
    """获取项目存储目录（用户数据目录下，确保不受打包影响）。"""
    return get_data_dir("projects")


PROJECTS_DIR = _get_projects_dir()


def _ensure_dir():
    """确保项目目录存在。"""
    os.makedirs(PROJECTS_DIR, exist_ok=True)


def _slugify(name):
    """将项目名称转为文件系统安全的 slug。"""
    slug = re.sub(r'[^\w\s一-鿿-]', '', name).strip()
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug if slug else "project"


def _project_path(slug):
    """获取项目文件的完整路径。"""
    return os.path.join(PROJECTS_DIR, f"{slug}.pbc_project.json")


def _serialize_state(state, file_renames=None, view_state=None):
    """将运行时 state 序列化为可 JSON 存储的字典。"""
    project_data = {
        "version": 2,
        "updated_at": datetime.now().isoformat(),
        "checklist": state.get("checklist"),
        "scanned_files": state.get("scanned_files"),
        "scanned_folders": state.get("scanned_folders"),
        "match_results": state.get("match_results"),
        "scan_root": state.get("scan_root"),
        "scan_source": state.get("scan_source", "local"),
        "scan_display_root": state.get("scan_display_root"),
        "cloud_source_url": state.get("cloud_source_url"),
        "new_items": state.get("new_items", []),
        "existing_items": state.get("existing_items", []),
        "previous_scanned_files": state.get("previous_scanned_files", []),
        "previous_scanned_folders": state.get("previous_scanned_folders", []),
        "pending_match_files": state.get("pending_match_files", []),
        "pending_match_folders": state.get("pending_match_folders", []),
        "scan_needs_match": state.get("scan_needs_match", False),
        "last_scan_diff": state.get("last_scan_diff"),
        "checklist_template": state.get("checklist_template"),
        "checklist_file_path": state.get("checklist_file_path"),
        "file_renames": file_renames or {},
        "frontend_view_state": view_state or {},
        "file_content_cache": state.get("file_content_cache", {}),
        "content_suggestions": state.get("content_suggestions", []),
        "content_ignored_files": state.get("content_ignored_files", []),
        "dev_logs": state.get("dev_logs", []),
        "scan_sources": state.get("scan_sources", []),
        "documents": state.get("documents", {}),
        "scan_snapshots": state.get("scan_snapshots", {}),
        "source_folders": state.get("source_folders", {}),
        "folder_requirement_mappings": state.get("folder_requirement_mappings", []),
        "organize_mappings": state.get("organize_mappings", []),
        "scan_file_statuses": state.get("scan_file_statuses", []),
    }
    return project_data


def _deserialize_state(project_data):
    """从 JSON 数据恢复运行时 state 字典。"""
    state = {
        "checklist": project_data.get("checklist"),
        "scanned_files": project_data.get("scanned_files"),
        "scanned_folders": project_data.get("scanned_folders"),
        "match_results": project_data.get("match_results"),
        "scan_root": project_data.get("scan_root"),
        "scan_source": project_data.get("scan_source", "local"),
        "scan_display_root": project_data.get("scan_display_root"),
        "cloud_source_url": project_data.get("cloud_source_url"),
        "new_items": project_data.get("new_items", []),
        "existing_items": project_data.get("existing_items", []),
        "previous_scanned_files": project_data.get("previous_scanned_files", []),
        "previous_scanned_folders": project_data.get("previous_scanned_folders", []),
        "pending_match_files": project_data.get("pending_match_files", []),
        "pending_match_folders": project_data.get("pending_match_folders", []),
        "scan_needs_match": project_data.get("scan_needs_match", False),
        "last_scan_diff": project_data.get("last_scan_diff"),
        "checklist_template": project_data.get("checklist_template"),
        "checklist_file_path": project_data.get("checklist_file_path"),
        "active_project": project_data.get("active_project"),
        "file_content_cache": project_data.get("file_content_cache", {}),
        "content_suggestions": project_data.get("content_suggestions", []),
        "content_ignored_files": project_data.get("content_ignored_files", []),
        "dev_logs": project_data.get("dev_logs", []),
        "scan_sources": project_data.get("scan_sources", []),
        "documents": project_data.get("documents", {}),
        "scan_snapshots": project_data.get("scan_snapshots", {}),
        "source_folders": project_data.get("source_folders", {}),
        "folder_requirement_mappings": project_data.get("folder_requirement_mappings", []),
        "organize_mappings": project_data.get("organize_mappings", []),
        "scan_file_statuses": project_data.get("scan_file_statuses", []),
    }
    return state


def list_projects():
    """列出所有已保存的项目，返回摘要列表。

    返回: list[dict] — 每项含 slug, name, updated_at, item_count, matched_count, total_count
    """
    _ensure_dir()
    projects = []
    for filename in os.listdir(PROJECTS_DIR):
        if not filename.endswith(".pbc_project.json"):
            continue
        filepath = os.path.join(PROJECTS_DIR, filename)
        slug = filename.replace(".pbc_project.json", "")
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        project_name = data.get("project_name", slug)
        updated_at = data.get("updated_at", "")
        items = data.get("checklist_template", {}).get("items", []) if data.get("checklist_template") else []
        match_results = data.get("match_results") or []
        total = len(match_results)
        matched = sum(1 for r in match_results if r.get("status") == "已获取")

        projects.append({
            "slug": slug,
            "name": project_name,
            "updated_at": updated_at,
            "item_count": len(items),
            "matched_count": matched,
            "total_count": total,
        })
    # 按更新时间倒序
    projects.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    return projects


def load_project(slug):
    """加载项目文件，返回完整数据字典。

    返回: dict | None — 含 state（运行时状态）、file_renames、view_state、project_name
    """
    _ensure_dir()
    filepath = _project_path(slug)
    if not os.path.isfile(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

    version = data.get("version", 0)
    if version < 1:
        return None

    state = _deserialize_state(data)
    state["active_project"] = {"slug": slug, "name": data.get("project_name", slug)}

    return {
        "state": state,
        "file_renames": data.get("file_renames", {}),
        "view_state": data.get("frontend_view_state", {}),
        "project_name": data.get("project_name", slug),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
    }


def save_project(slug, state, file_renames=None, view_state=None):
    """将当前状态保存到项目文件。文件已存在时更新，不存在时新建。

    返回: str — 项目文件路径
    """
    _ensure_dir()
    filepath = _project_path(slug)

    # 如果是已有项目，保留原始创建时间
    created_at = None
    project_name = slug
    if os.path.isfile(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)
            created_at = existing.get("created_at")
            project_name = existing.get("project_name", slug)
        except (json.JSONDecodeError, IOError):
            pass

    if created_at is None:
        created_at = datetime.now().isoformat()

    # 从 active_project 获取项目名称
    active = state.get("active_project") or {}
    if active.get("name"):
        project_name = active["name"]

    project_data = _serialize_state(state, file_renames, view_state)
    project_data["project_name"] = project_name
    project_data["created_at"] = created_at
    project_data["active_project"] = state.get("active_project")

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(project_data, f, ensure_ascii=False, indent=2, default=str)

    return filepath


def create_project(name, state, file_renames=None, view_state=None):
    """创建新的项目文件。

    返回: dict — {"slug": str, "file_path": str}
    """
    slug = _slugify(name)
    # 如果 slug 已存在，追加数字后缀
    base_slug = slug
    counter = 1
    while os.path.isfile(_project_path(slug)):
        slug = f"{base_slug}-{counter}"
        counter += 1

    state["active_project"] = {"slug": slug, "name": name}

    project_data = _serialize_state(state, file_renames, view_state)
    project_data["project_name"] = name
    project_data["created_at"] = datetime.now().isoformat()
    project_data["active_project"] = state["active_project"]

    _ensure_dir()
    with open(_project_path(slug), "w", encoding="utf-8") as f:
        json.dump(project_data, f, ensure_ascii=False, indent=2, default=str)

    return {"slug": slug, "file_path": _project_path(slug)}


def delete_project(slug):
    """删除项目文件。

    返回: bool — 是否成功删除
    """
    filepath = _project_path(slug)
    if not os.path.isfile(filepath):
        return False
    os.remove(filepath)
    return True
