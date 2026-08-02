"""Persistent document identity, version and location tracking.

The existing matcher works with paths.  This module adds a compatibility layer:
paths remain usable while documents keep a stable identity across edits, moves,
duplicates and organization into a second project source.
"""

import hashlib
import os
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _structure_summary(path):
    extension = os.path.splitext(path)[1].lower()
    try:
        if extension == ".xlsx":
            with zipfile.ZipFile(path) as archive:
                sheet_names = []
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                for node in workbook.iter():
                    if node.tag.endswith("}sheet"):
                        sheet_names.append(node.attrib.get("name", ""))
                sheets = []
                worksheet_names = sorted(
                    name for name in archive.namelist()
                    if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
                )
                for index, name in enumerate(worksheet_names):
                    data = archive.read(name)
                    root = ET.fromstring(data)
                    dimension = next(
                        (node.attrib.get("ref", "") for node in root.iter()
                         if node.tag.endswith("}dimension")),
                        "",
                    )
                    sheets.append({
                        "name": sheet_names[index] if index < len(sheet_names) else os.path.basename(name),
                        "dimension": dimension,
                        "formula_count": data.count(b"<f"),
                    })
                return {"type": "excel", "sheets": sheets}
        if extension == ".docx":
            with zipfile.ZipFile(path) as archive:
                data = archive.read("word/document.xml")
            return {
                "type": "word",
                "paragraph_count": data.count(b"<w:p"),
                "table_count": data.count(b"<w:tbl"),
            }
        if extension == ".pdf":
            import fitz
            with fitz.open(path) as document:
                return {"type": "pdf", "page_count": document.page_count}
        if extension in (".zip", ".7z", ".rar"):
            if extension == ".zip":
                with zipfile.ZipFile(path) as archive:
                    names = sorted(
                        info.filename for info in archive.infolist() if not info.is_dir()
                    )
                return {"type": "archive", "entries": names[:5000], "entry_count": len(names)}
    except Exception as exc:
        return {"type": "unknown", "error": str(exc)}
    return {}


def files_have_same_content(first, second):
    if not os.path.isfile(first) or not os.path.isfile(second):
        return False
    if os.path.getsize(first) != os.path.getsize(second):
        return False
    return _sha256(first) == _sha256(second)


def next_version_path(path):
    base, extension = os.path.splitext(path)
    counter = 2
    candidate = f"{base}_v{counter}{extension}"
    while os.path.exists(candidate):
        counter += 1
        candidate = f"{base}_v{counter}{extension}"
    return candidate


def ensure_registry(state):
    state.setdefault("scan_sources", [])
    state.setdefault("documents", {})
    state.setdefault("scan_snapshots", {})
    state.setdefault("folder_requirement_mappings", [])
    state.setdefault("organize_mappings", [])
    return state


def register_source(state, root, source_type="local", role=None, display_root=None,
                    web_url=None):
    ensure_registry(state)
    identity = web_url.rstrip("/") if web_url else _norm(root)
    for source in state["scan_sources"]:
        if source.get("identity") == identity:
            source.update({
                "root": root,
                "display_root": display_root or web_url or root,
                "type": source_type,
                "role": role or source.get("role", "inbox"),
                "web_url": web_url,
                "active": True,
            })
            return source
    source = {
        "id": "src_" + uuid.uuid4().hex,
        "identity": identity,
        "type": source_type,
        "role": role or "inbox",
        "root": root,
        "display_root": display_root or web_url or root,
        "web_url": web_url,
        "active": True,
        "created_at": _now(),
    }
    state["scan_sources"].append(source)
    return source


def _location(document, source_id, path):
    normalized = _norm(path)
    for location in document.get("locations", []):
        if location.get("source_id") == source_id and _norm(location.get("path", "")) == normalized:
            return location
    return None


def _document_by_hash(state, content_hash, company_context=""):
    for document in state.get("documents", {}).values():
        existing_context = document.get("company_context", "")
        if company_context and existing_context and company_context != existing_context:
            continue
        if document.get("current_hash") == content_hash:
            return document
        if any(version.get("content_hash") == content_hash for version in document.get("versions", [])):
            return document
    return None


def _document_by_id(state, document_id):
    return state.get("documents", {}).get(document_id)


def _new_document(path, content_hash, size, mtime, company_context=""):
    document_id = "doc_" + uuid.uuid4().hex
    version_id = "ver_" + uuid.uuid4().hex
    return {
        "id": document_id,
        "name": os.path.basename(path),
        "current_hash": content_hash,
        "current_version_id": version_id,
        "preferred_path": path,
        "versions": [{
            "id": version_id,
            "content_hash": content_hash,
            "size": size,
            "modified_time": mtime,
            "created_at": _now(),
            "status": "current",
            "structure": _structure_summary(path),
        }],
        "locations": [],
        "first_seen_at": _now(),
        "last_seen_at": _now(),
        "change_status": "new",
        "company_context": company_context,
    }


def _add_version(document, path, content_hash, size, mtime):
    for version in document.get("versions", []):
        if version.get("content_hash") == content_hash:
            document["current_hash"] = content_hash
            document["current_version_id"] = version["id"]
            return version, False
    for version in document.get("versions", []):
        if version.get("status") == "current":
            version["status"] = "history"
    version = {
        "id": "ver_" + uuid.uuid4().hex,
        "content_hash": content_hash,
        "size": size,
        "modified_time": mtime,
        "created_at": _now(),
        "status": "current",
        "structure": _structure_summary(path),
    }
    document.setdefault("versions", []).append(version)
    document["current_hash"] = content_hash
    document["current_version_id"] = version["id"]
    return version, True


def reconcile_source(state, source, files, metadata_by_path=None, company_names=None):
    """Reconcile one source and return path-level changes.

    Existing-path content edits create a version but are deliberately excluded
    from ``new_files`` so confirmed requirement links are retained and LLM/OCR
    matching is not automatically triggered.
    """
    ensure_registry(state)
    metadata_by_path = metadata_by_path or {}
    company_names = company_names or []
    source_id = source["id"]
    old_snapshot = state["scan_snapshots"].get(source_id, {})
    new_snapshot = {}
    current_norms = {_norm(path) for path in files}
    changes = {
        "new_files": [],
        "updated_files": [],
        "moved_files": [],
        "duplicate_files": [],
        "removed_files": [],
        "unchanged_files": [],
        "errors": [],
    }

    for path in files:
        normalized = _norm(path)
        document = None
        content_hash = ""
        try:
            stat = os.stat(path)
            size = stat.st_size
            mtime = stat.st_mtime_ns
        except OSError as exc:
            changes["errors"].append({"path": path, "error": str(exc)})
            continue
        try:
            relative_parts = os.path.relpath(path, source.get("root") or "").split(os.sep)
        except ValueError:
            relative_parts = []
        company_context = next(
            (company for company in company_names if company in relative_parts),
            "",
        )

        previous = old_snapshot.get(normalized)
        if previous and previous.get("size") == size and previous.get("mtime") == mtime:
            document = _document_by_id(state, previous.get("document_id"))
            if document:
                content_hash = previous.get("content_hash", "")
                changes["unchanged_files"].append(path)
            else:
                previous = None

        if not previous:
            try:
                content_hash = _sha256(path)
            except OSError as exc:
                changes["errors"].append({"path": path, "error": str(exc)})
                continue
            document = _document_by_hash(state, content_hash, company_context)
            if document:
                active_locations = [
                    loc for loc in document.get("locations", [])
                    if loc.get("available") and _norm(loc.get("path", "")) != normalized
                ]
                active_current = [
                    loc for loc in active_locations
                    if loc.get("source_id") != source_id
                    or _norm(loc.get("path", "")) in current_norms
                ]
                if active_current:
                    changes["duplicate_files"].append(path)
                else:
                    changes["moved_files"].append({
                        "from": document.get("preferred_path", ""),
                        "to": path,
                    })
            else:
                document = _new_document(path, content_hash, size, mtime, company_context)
                state["documents"][document["id"]] = document
                changes["new_files"].append(path)
        else:
            content_hash = previous.get("content_hash", "")
            document = document or _document_by_id(state, previous.get("document_id"))
            if not document:
                document = _new_document(
                    path, content_hash or _sha256(path), size, mtime, company_context
                )
                state["documents"][document["id"]] = document
                changes["new_files"].append(path)
            elif previous.get("size") != size or previous.get("mtime") != mtime:
                content_hash = _sha256(path)
                if content_hash != previous.get("content_hash"):
                    _add_version(document, path, content_hash, size, mtime)
                    document["change_status"] = "content_updated"
                    changes["updated_files"].append(path)
                else:
                    changes["unchanged_files"].append(path)

        location = _location(document, source_id, path)
        relative_path = os.path.relpath(path, source.get("root") or os.path.dirname(path))
        if location is None:
            location = {
                "id": "loc_" + uuid.uuid4().hex,
                "source_id": source_id,
                "path": path,
                "relative_path": relative_path,
                "role": source.get("role", "inbox"),
                "web_url": None,
                "available": True,
                "first_seen_at": _now(),
            }
            document.setdefault("locations", []).append(location)
        location.update({
            "path": path,
            "relative_path": relative_path,
            "available": True,
            "last_seen_at": _now(),
            "version_id": document.get("current_version_id"),
        })
        cloud_metadata = metadata_by_path.get(path) or {}
        if cloud_metadata:
            location.update(cloud_metadata)
        document["last_seen_at"] = _now()
        if source.get("role") == "organized" or not document.get("preferred_path"):
            document["preferred_path"] = path
        new_snapshot[normalized] = {
            "path": path,
            "size": size,
            "mtime": mtime,
            "content_hash": content_hash,
            "document_id": document["id"],
        }

    for normalized, previous in old_snapshot.items():
        if normalized in current_norms:
            continue
        changes["removed_files"].append(previous.get("path", normalized))
        document = _document_by_id(state, previous.get("document_id"))
        if document:
            for location in document.get("locations", []):
                if location.get("source_id") == source_id and _norm(location.get("path", "")) == normalized:
                    location["available"] = False
                    location["last_seen_at"] = _now()
            available = [loc for loc in document.get("locations", []) if loc.get("available")]
            if available and _norm(document.get("preferred_path", "")) == normalized:
                organized = [loc for loc in available if loc.get("role") == "organized"]
                document["preferred_path"] = (organized or available)[0]["path"]

    state["scan_snapshots"][source_id] = new_snapshot
    return changes


def all_available_files(state):
    ensure_registry(state)
    paths = []
    for document in state["documents"].values():
        for location in document.get("locations", []):
            path = location.get("path")
            if location.get("available") and path and path not in paths:
                paths.append(path)
    return paths


def resolve_preferred_path(state, path):
    normalized = _norm(path)
    for document in state.get("documents", {}).values():
        if any(_norm(loc.get("path", "")) == normalized for loc in document.get("locations", [])):
            preferred = document.get("preferred_path")
            if preferred and os.path.exists(preferred):
                return preferred
            available = [
                loc.get("path") for loc in document.get("locations", [])
                if loc.get("available") and loc.get("path") and os.path.exists(loc.get("path"))
            ]
            if available:
                return available[0]
    return path


def resolve_export_link(state, path):
    normalized = _norm(path)
    for document in state.get("documents", {}).values():
        locations = document.get("locations", [])
        if not any(_norm(loc.get("path", "")) == normalized for loc in locations):
            continue
        preferred = document.get("preferred_path")
        if preferred and os.path.exists(preferred):
            preferred_location = next(
                (loc for loc in locations if _norm(loc.get("path", "")) == _norm(preferred)),
                None,
            )
            if preferred_location and preferred_location.get("role") == "organized":
                return "file:///" + preferred.replace("\\", "/")
        cloud = next((loc for loc in locations if loc.get("web_url")), None)
        if cloud:
            return cloud["web_url"]
        if preferred and os.path.exists(preferred):
            return "file:///" + preferred.replace("\\", "/")
    return "file:///" + path.replace("\\", "/")


def document_status_for_path(state, path):
    normalized = _norm(path)
    for document in state.get("documents", {}).values():
        if any(_norm(loc.get("path", "")) == normalized for loc in document.get("locations", [])):
            return {
                "document_id": document.get("id"),
                "change_status": document.get("change_status", ""),
                "version_count": len(document.get("versions", [])),
                "location_count": len([
                    loc for loc in document.get("locations", []) if loc.get("available")
                ]),
                "preferred_path": document.get("preferred_path"),
            }
    return {}


def document_change_detail(state, path):
    normalized = _norm(path)
    for document in state.get("documents", {}).values():
        if not any(_norm(loc.get("path", "")) == normalized for loc in document.get("locations", [])):
            continue
        versions = document.get("versions", [])
        if len(versions) < 2:
            return {"available": False, "message": "没有可比较的历史版本"}
        previous, current = versions[-2], versions[-1]
        old_structure = previous.get("structure") or {}
        new_structure = current.get("structure") or {}
        detail = {
            "available": bool(old_structure or new_structure),
            "previous": {
                "size": previous.get("size"),
                "modified_time": previous.get("modified_time"),
                "structure": old_structure,
            },
            "current": {
                "size": current.get("size"),
                "modified_time": current.get("modified_time"),
                "structure": new_structure,
            },
            "changes": [],
        }
        if old_structure.get("type") == "excel" and new_structure.get("type") == "excel":
            old_sheets = {item["name"]: item for item in old_structure.get("sheets", [])}
            new_sheets = {item["name"]: item for item in new_structure.get("sheets", [])}
            for name in sorted(new_sheets.keys() - old_sheets.keys()):
                detail["changes"].append(f"新增 Sheet：{name}")
            for name in sorted(old_sheets.keys() - new_sheets.keys()):
                detail["changes"].append(f"删除 Sheet：{name}")
            for name in sorted(old_sheets.keys() & new_sheets.keys()):
                old, new = old_sheets[name], new_sheets[name]
                if old.get("dimension") != new.get("dimension"):
                    detail["changes"].append(
                        f"{name} 使用区域：{old.get('dimension') or '空'} → {new.get('dimension') or '空'}"
                    )
                if old.get("formula_count") != new.get("formula_count"):
                    detail["changes"].append(
                        f"{name} 公式数：{old.get('formula_count', 0)} → {new.get('formula_count', 0)}"
                    )
        elif old_structure.get("type") == new_structure.get("type"):
            for key in ("paragraph_count", "table_count", "page_count", "entry_count"):
                if old_structure.get(key) != new_structure.get(key):
                    detail["changes"].append(
                        f"{key}：{old_structure.get(key, 0)} → {new_structure.get(key, 0)}"
                    )
        return detail
    return {"available": False, "message": "未找到资料记录"}


def relative_display_paths(state):
    sources = {item.get("id"): item for item in state.get("scan_sources", [])}
    labels = {
        "organized": "整理后资料库",
        "archive_cache": "压缩包内容",
        "inbox": "客户来件",
    }
    result = {}
    for document in state.get("documents", {}).values():
        for location in document.get("locations", []):
            if not location.get("available") or not location.get("path"):
                continue
            source = sources.get(location.get("source_id"), {})
            label = labels.get(source.get("role"), source.get("display_root") or "资料来源")
            result[location["path"]] = os.path.join(
                str(label), location.get("relative_path") or os.path.basename(location["path"])
            )
    return result


def record_organized_location(state, source_path, destination, organized_source):
    """Attach a copied/organized location to the same document identity."""
    ensure_registry(state)
    source_norm = _norm(source_path)
    document = None
    for candidate in state["documents"].values():
        if any(_norm(loc.get("path", "")) == source_norm for loc in candidate.get("locations", [])):
            document = candidate
            break
    if document is None and os.path.isfile(source_path):
        stat = os.stat(source_path)
        content_hash = _sha256(source_path)
        document = _new_document(source_path, content_hash, stat.st_size, stat.st_mtime_ns)
        state["documents"][document["id"]] = document
    if document is None:
        return None
    location = _location(document, organized_source["id"], destination)
    if location is None:
        location = {
            "id": "loc_" + uuid.uuid4().hex,
            "source_id": organized_source["id"],
            "path": destination,
            "relative_path": os.path.relpath(destination, organized_source["root"]),
            "role": "organized",
            "available": True,
            "first_seen_at": _now(),
        }
        document.setdefault("locations", []).append(location)
    location.update({
        "available": True,
        "last_seen_at": _now(),
        "version_id": document.get("current_version_id"),
    })
    document["preferred_path"] = destination
    state["organize_mappings"].append({
        "document_id": document["id"],
        "source": source_path,
        "destination": destination,
        "created_at": _now(),
    })
    return document


def move_registered_location(state, old_path, new_path):
    old_normalized = _norm(old_path)
    for document in state.get("documents", {}).values():
        for location in document.get("locations", []):
            if _norm(location.get("path", "")) == old_normalized:
                location["path"] = new_path
                location["relative_path"] = os.path.basename(new_path)
                location["last_seen_at"] = _now()
                if _norm(document.get("preferred_path", "")) == old_normalized:
                    document["preferred_path"] = new_path
                return document
    return None


def record_historical_version(state, current_source_path, historical_path, organized_source):
    """Attach an existing conflicting destination as a history version."""
    if not os.path.isfile(historical_path):
        return None
    current_normalized = _norm(current_source_path)
    document = next(
        (
            candidate for candidate in state.get("documents", {}).values()
            if any(_norm(loc.get("path", "")) == current_normalized
                   for loc in candidate.get("locations", []))
        ),
        None,
    )
    if document is None:
        return None
    stat = os.stat(historical_path)
    content_hash = _sha256(historical_path)
    version = next(
        (item for item in document.get("versions", [])
         if item.get("content_hash") == content_hash),
        None,
    )
    if version is None:
        version = {
            "id": "ver_" + uuid.uuid4().hex,
            "content_hash": content_hash,
            "size": stat.st_size,
            "modified_time": stat.st_mtime_ns,
            "created_at": _now(),
            "status": "history",
            "structure": _structure_summary(historical_path),
        }
        document.setdefault("versions", []).insert(0, version)
    location = _location(document, organized_source["id"], historical_path)
    if location is None:
        location = {
            "id": "loc_" + uuid.uuid4().hex,
            "source_id": organized_source["id"],
            "path": historical_path,
            "relative_path": os.path.relpath(historical_path, organized_source["root"]),
            "role": "organized_history",
            "available": True,
            "first_seen_at": _now(),
        }
        document.setdefault("locations", []).append(location)
    location.update({
        "version_id": version["id"],
        "available": True,
        "last_seen_at": _now(),
    })
    return document


def remap_match_paths(state, path_mapping):
    if not path_mapping:
        return
    normalized_mapping = {_norm(old): new for old, new in path_mapping.items()}
    for result in state.get("match_results") or []:
        result["matched_files"] = [
            normalized_mapping.get(_norm(path), path)
            for path in result.get("matched_files", [])
        ]
        result["matched_names"] = [
            os.path.basename(path) for path in result.get("matched_files", [])
        ]
        for company_data in (result.get("company_coverage") or {}).values():
            for key in ("files", "folders"):
                company_data[key] = [
                    normalized_mapping.get(_norm(path), path)
                    for path in company_data.get(key, [])
                ]
