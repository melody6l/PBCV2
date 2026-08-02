"""Safe archive expansion for scan-time discovery."""

import hashlib
import os
import shutil
import zipfile

from path_utils import get_bundle_path, get_data_dir


MAX_FILES = 5000
MAX_TOTAL_SIZE = 2 * 1024 * 1024 * 1024
MAX_SINGLE_SIZE = 512 * 1024 * 1024


class ArchiveLimitError(RuntimeError):
    pass


def _safe_destination(root, member_name):
    destination = os.path.abspath(os.path.join(root, member_name))
    root_abs = os.path.abspath(root)
    if os.path.commonpath([root_abs, destination]) != root_abs:
        raise ArchiveLimitError("压缩包包含不安全的路径")
    return destination


def _validate_members(members):
    if len(members) > MAX_FILES:
        raise ArchiveLimitError(f"压缩包文件数超过限制（{MAX_FILES}）")
    total = 0
    for name, size, is_dir in members:
        if is_dir:
            continue
        if size > MAX_SINGLE_SIZE:
            raise ArchiveLimitError("压缩包中存在超大文件")
        total += max(0, size)
        if total > MAX_TOTAL_SIZE:
            raise ArchiveLimitError("解压后总容量超过限制")


def _extract_zip(path, destination, password=None):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        members = [(info.filename, info.file_size, info.is_dir()) for info in infos]
        _validate_members(members)
        for info in infos:
            target = _safe_destination(destination, info.filename)
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                with archive.open(info, pwd=password.encode() if password else None) as source:
                    with open(target, "wb") as output:
                        shutil.copyfileobj(source, output)
            except RuntimeError as exc:
                if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
                    raise PermissionError("压缩包需要密码") from exc
                raise


def _extract_7z(path, destination, password=None):
    try:
        import py7zr
    except ImportError as exc:
        raise RuntimeError("7z解压组件未包含在当前程序中") from exc
    try:
        with py7zr.SevenZipFile(path, mode="r", password=password) as archive:
            entries = archive.list()
            members = [
                (entry.filename, int(entry.uncompressed or 0), bool(entry.is_directory))
                for entry in entries
            ]
            _validate_members(members)
            for name, _, _ in members:
                _safe_destination(destination, name)
            archive.extractall(path=destination)
    except Exception as exc:
        if "password" in str(exc).lower():
            raise PermissionError("压缩包需要密码") from exc
        raise


def _extract_rar(path, destination, password=None):
    try:
        import rarfile
    except ImportError as exc:
        raise RuntimeError("RAR解压组件未包含在当前程序中") from exc
    bundled_candidates = [
        get_bundle_path(os.path.join("tools", "7zip", "7zz.exe")),
        get_bundle_path(os.path.join("tools", "7zip", "7zz")),
    ]
    bundled_tool = next((candidate for candidate in bundled_candidates if os.path.isfile(candidate)), None)
    if bundled_tool:
        rarfile.SEVENZIP2_TOOL = bundled_tool
        rarfile.FORCE_TOOL = True
    try:
        with rarfile.RarFile(path) as archive:
            infos = archive.infolist()
            members = [(info.filename, info.file_size, info.isdir()) for info in infos]
            _validate_members(members)
            for name, _, _ in members:
                _safe_destination(destination, name)
            archive.extractall(path=destination, pwd=password)
    except rarfile.PasswordRequired as exc:
        raise PermissionError("压缩包需要密码") from exc
    except rarfile.RarCannotExec as exc:
        raise RuntimeError("RAR解压组件不可用") from exc


def expand_archives(scan_root, files, passwords=None):
    """Extract supported archives into a managed cache and return scan paths."""
    passwords = passwords or {}
    root_key = hashlib.sha256(os.path.abspath(scan_root).encode("utf-8")).hexdigest()[:16]
    cache_root = get_data_dir("archive_cache", root_key)
    extracted_files = []
    extracted_folders = []
    statuses = []
    for path in files:
        extension = os.path.splitext(path)[1].lower()
        if extension not in (".zip", ".7z", ".rar"):
            continue
        try:
            stat = os.stat(path)
            archive_key = hashlib.sha256(
                f"{os.path.abspath(path)}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
            ).hexdigest()[:20]
            destination = os.path.join(cache_root, archive_key)
            marker = os.path.join(destination, ".pbc_complete")
            if not os.path.isfile(marker):
                temp_destination = destination + ".extracting"
                if os.path.isdir(temp_destination):
                    shutil.rmtree(temp_destination)
                os.makedirs(temp_destination, exist_ok=True)
                password = passwords.get(path)
                if extension == ".zip":
                    _extract_zip(path, temp_destination, password)
                elif extension == ".7z":
                    _extract_7z(path, temp_destination, password)
                else:
                    _extract_rar(path, temp_destination, password)
                if os.path.isdir(destination):
                    shutil.rmtree(destination)
                os.replace(temp_destination, destination)
                with open(marker, "w", encoding="utf-8") as stream:
                    stream.write("ok")
            for root, dirs, names in os.walk(destination):
                dirs[:] = [name for name in dirs if not name.startswith((".", "~"))]
                for dirname in dirs:
                    extracted_folders.append(os.path.join(root, dirname))
                for name in names:
                    if not name.startswith((".", "~")):
                        extracted_files.append(os.path.join(root, name))
            statuses.append({
                "path": path,
                "status": "extracted",
                "file_count": sum(1 for item in extracted_files if item.startswith(destination)),
            })
        except PermissionError as exc:
            statuses.append({"path": path, "status": "password_required", "detail": str(exc)})
        except (ArchiveLimitError, zipfile.BadZipFile) as exc:
            statuses.append({"path": path, "status": "failed", "detail": str(exc)})
        except Exception as exc:
            statuses.append({"path": path, "status": "component_unavailable", "detail": str(exc)})
    return {
        "cache_root": cache_root,
        "files": extracted_files,
        "folders": extracted_folders,
        "statuses": statuses,
    }
