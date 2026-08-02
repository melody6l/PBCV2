"""SharePoint / OneDrive folder access through Microsoft Graph.

Cloud folders are mirrored below the application data directory so the rest of
the application can keep using its existing local-path based matching, preview,
content extraction and export pipeline.
"""

import base64
import hashlib
import os
import re
import shutil
import threading
from urllib.parse import parse_qs, unquote, urlparse

import requests

from path_utils import get_data_dir


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
SCOPES = ["Files.Read.All", "Sites.Read.All", "User.Read"]
_auth_lock = threading.Lock()
_msal_app = None
_pending_flow = None


class CloudFolderError(RuntimeError):
    pass


def _client_id():
    return os.environ.get("PBC_MICROSOFT_CLIENT_ID", "").strip()


def _tenant():
    return os.environ.get("PBC_MICROSOFT_TENANT_ID", "organizations").strip() or "organizations"


def is_configured():
    return bool(_client_id())


def _get_msal_app():
    global _msal_app
    if not is_configured():
        raise CloudFolderError(
            "尚未配置 Microsoft 登录。请设置 PBC_MICROSOFT_CLIENT_ID；"
            "可选设置 PBC_MICROSOFT_TENANT_ID。"
        )
    try:
        import msal
    except ImportError as exc:
        raise CloudFolderError("缺少 msal 依赖，请重新安装 requirements.txt") from exc
    with _auth_lock:
        if _msal_app is None:
            _msal_app = msal.PublicClientApplication(
                _client_id(),
                authority=f"https://login.microsoftonline.com/{_tenant()}",
            )
    return _msal_app


def auth_status():
    if not is_configured():
        return {"configured": False, "signed_in": False}
    app = _get_msal_app()
    accounts = app.get_accounts()
    return {
        "configured": True,
        "signed_in": bool(accounts),
        "account": accounts[0].get("username", "") if accounts else "",
    }


def begin_device_login():
    global _pending_flow
    app = _get_msal_app()
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise CloudFolderError(flow.get("error_description") or "无法启动 Microsoft 登录")
    with _auth_lock:
        _pending_flow = flow
    return {
        "user_code": flow["user_code"],
        "verification_uri": flow.get("verification_uri", "https://microsoft.com/devicelogin"),
        "message": flow.get("message", ""),
        "expires_in": flow.get("expires_in", 900),
    }


def complete_device_login():
    global _pending_flow
    with _auth_lock:
        flow = _pending_flow
    if not flow:
        raise CloudFolderError("登录请求不存在或已过期，请重新登录")
    result = _get_msal_app().acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise CloudFolderError(result.get("error_description") or "Microsoft 登录未完成")
    with _auth_lock:
        _pending_flow = None
    return {"signed_in": True, "account": result.get("id_token_claims", {}).get("preferred_username", "")}


def _access_token():
    app = _get_msal_app()
    accounts = app.get_accounts()
    if not accounts:
        raise CloudFolderError("请先登录 Microsoft 账户")
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        raise CloudFolderError("Microsoft 登录已过期，请重新登录")
    return result["access_token"]


class GraphClient:
    def __init__(self, token):
        self.headers = {"Authorization": f"Bearer {token}"}

    def get(self, path_or_url, *, stream=False):
        url = path_or_url if path_or_url.startswith("http") else GRAPH_ROOT + path_or_url
        response = requests.get(url, headers=self.headers, timeout=60, stream=stream)
        if response.status_code >= 400:
            try:
                detail = response.json()["error"]["message"]
            except Exception:
                detail = response.text[:300]
            raise CloudFolderError(f"Microsoft Graph 请求失败 ({response.status_code}): {detail}")
        return response

    def json(self, path_or_url):
        return self.get(path_or_url).json()

    def paged(self, path_or_url):
        url = path_or_url
        while url:
            data = self.json(url)
            yield from data.get("value", [])
            url = data.get("@odata.nextLink")


def _share_id(url):
    encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return "u!" + encoded


def _clean_web_url(url):
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise CloudFolderError("请输入有效的 SharePoint 或 OneDrive 文件夹网址")
    host = parsed.netloc.lower()
    if "sharepoint.com" not in host and "1drv.ms" not in host and "onedrive.live.com" not in host:
        raise CloudFolderError("目前仅支持 SharePoint 和 OneDrive 网址")
    return url.strip()


def _resolve_via_share(client, url):
    return client.json(f"/shares/{_share_id(url)}/driveItem?$expand=parentReference")


def _candidate_server_path(url):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    raw = query.get("id", [""])[0] or query.get("RootFolder", [""])[0]
    if raw:
        return unquote(raw)
    path = unquote(parsed.path)
    # Modern sharing links sometimes prefix the actual path with /:f:/r.
    path = re.sub(r"^/:[a-z]:/r", "", path, flags=re.I)
    return path


def _resolve_via_site_drives(client, url):
    parsed = urlparse(url)
    server_path = _candidate_server_path(url)
    match = re.match(r"^/(sites|teams|personal)/[^/]+", server_path, flags=re.I)
    if not match:
        raise CloudFolderError("无法从网址识别 SharePoint 站点")
    site_path = match.group(0)
    site = client.json(f"/sites/{parsed.netloc}:{site_path}")
    drives = list(client.paged(f"/sites/{site['id']}/drives"))
    normalized_url = unquote(url.split("?", 1)[0]).rstrip("/").lower()
    normalized_path = server_path.rstrip("/").lower()
    for drive in sorted(drives, key=lambda d: len(d.get("webUrl", "")), reverse=True):
        drive_web_path = unquote(urlparse(drive.get("webUrl", "")).path).rstrip("/")
        relative = ""
        if normalized_path == drive_web_path.lower():
            relative = ""
        elif normalized_path.startswith(drive_web_path.lower() + "/"):
            relative = server_path[len(drive_web_path):].lstrip("/")
        elif normalized_url.startswith(unquote(drive.get("webUrl", "")).rstrip("/").lower()):
            relative = unquote(urlparse(url).path)[len(drive_web_path):].lstrip("/")
        else:
            continue
        if not relative:
            return client.json(f"/drives/{drive['id']}/root")
        from urllib.parse import quote
        return client.json(f"/drives/{drive['id']}/root:/{quote(relative, safe='/')}")
    raise CloudFolderError("网址中的文档库或文件夹无法识别，请使用该文件夹的“复制链接”网址")


def resolve_folder(client, url):
    url = _clean_web_url(url)
    try:
        item = _resolve_via_share(client, url)
    except CloudFolderError:
        item = _resolve_via_site_drives(client, url)
    if "folder" not in item:
        raise CloudFolderError("该网址指向文件而不是文件夹")
    drive_id = item.get("parentReference", {}).get("driveId")
    if not drive_id:
        raise CloudFolderError("无法确定云端文件夹所属的文档库")
    return drive_id, item


def _safe_name(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip()
    return cleaned or "未命名"


def _cache_root(url, folder_name):
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(get_data_dir("cloud_cache"), digest, _safe_name(folder_name))


def mirror_folder(url):
    """Recursively mirror a cloud folder and return local file/folder paths."""
    client = GraphClient(_access_token())
    drive_id, root_item = resolve_folder(client, url)
    root = _cache_root(url, root_item.get("name", "云端资料"))
    os.makedirs(root, exist_ok=True)
    files = []
    folders = []
    file_metadata = {}
    seen_local_paths = {os.path.normcase(os.path.abspath(root))}

    def unique_path(parent, name):
        path = os.path.join(parent, _safe_name(name))
        base, ext = os.path.splitext(path)
        candidate = path
        counter = 2
        while os.path.normcase(os.path.abspath(candidate)) in seen_local_paths:
            candidate = f"{base} ({counter}){ext}"
            counter += 1
        seen_local_paths.add(os.path.normcase(os.path.abspath(candidate)))
        return candidate

    def walk(item_id, local_parent):
        endpoint = f"/drives/{drive_id}/items/{item_id}/children"
        for child in client.paged(endpoint):
            if str(child.get("name", "")).startswith((".", "~")):
                continue
            local_path = unique_path(local_parent, child.get("name"))
            if "folder" in child:
                os.makedirs(local_path, exist_ok=True)
                folders.append(local_path)
                walk(child["id"], local_path)
            elif "file" in child:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                temp_path = local_path + ".pbc-download"
                response = client.get(f"/drives/{drive_id}/items/{child['id']}/content", stream=True)
                with open(temp_path, "wb") as output:
                    shutil.copyfileobj(response.raw, output)
                os.replace(temp_path, local_path)
                files.append(local_path)
                file_metadata[local_path] = {
                    "web_url": child.get("webUrl"),
                    "drive_id": drive_id,
                    "item_id": child.get("id"),
                    "etag": child.get("eTag"),
                    "modified_time": child.get("lastModifiedDateTime"),
                }

    walk(root_item["id"], root)
    # Remove entries deleted in the cloud only after a successful traversal.
    for current_root, dir_names, file_names in os.walk(root, topdown=False):
        for file_name in file_names:
            path = os.path.join(current_root, file_name)
            if os.path.normcase(os.path.abspath(path)) not in seen_local_paths:
                try:
                    os.remove(path)
                except OSError:
                    pass
        for dir_name in dir_names:
            path = os.path.join(current_root, dir_name)
            if os.path.normcase(os.path.abspath(path)) not in seen_local_paths:
                try:
                    shutil.rmtree(path)
                except OSError:
                    pass
    return {
        "root_path": root,
        "display_root": url,
        "files": files,
        "folders": folders,
        "drive_id": drive_id,
        "root_item_id": root_item["id"],
        "folder_name": root_item.get("name", ""),
        "file_metadata": file_metadata,
    }
