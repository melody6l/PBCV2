"""会话管理模块 — 内存 LRU 缓存 + 磁盘永久持久化。

每个浏览器标签页对应一个会话（session），通过 X-Session-Id 头识别。
- 内存中只保留最近访问的 N 个会话（默认 20），防止内存泄漏。
- 每次 API 请求后自动将状态防抖写入磁盘（sessions/<uuid>.json），防止重启丢失。
- 磁盘文件永久保留，除非用户主动删除。
- 访问冷会话时从磁盘惰性加载回内存。
"""

import json
import os
import threading
import time
import uuid as _uuid


# ====== 创建空白状态 ======

def create_fresh_state():
    """创建全新的空白状态字典（与 app.py 原有定义完全一致）。"""
    return {
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
        "active_project": None,
    }


# ====== SessionStore ======

class SessionStore:
    """会话存储：内存 LRU 缓存 + 磁盘永久持久化。"""

    def __init__(self, sessions_dir, max_memory=20, save_delay=5.0):
        """初始化会话存储。

        Args:
            sessions_dir: 磁盘会话文件存放目录路径
            max_memory: 内存中最多保留的会话数（超出按 LRU 淘汰，不删磁盘文件）
            save_delay: 防抖保存延迟（秒），连续请求会重置计时器
        """
        self._sessions = {}          # {sid: {"state": dict, "last_access": float, "dirty": bool}}
        self._sessions_dir = sessions_dir
        self._max_memory = max_memory
        self._save_delay = save_delay
        self._lock = threading.Lock()
        self._save_timers = {}       # {sid: threading.Timer}
        self._shutdown = False

        os.makedirs(self._sessions_dir, exist_ok=True)

    # ------ 公开 API ------

    def get(self, sid):
        """获取会话状态。内存命中则直接返回；未命中则尝试磁盘加载；
        都未命中则创建新会话。

        Returns:
            dict: 会话状态字典（可变更，修改会自动反映到下次保存中）
        """
        with self._lock:
            if self._shutdown:
                return create_fresh_state()

            # 1. 内存命中
            if sid in self._sessions:
                entry = self._sessions[sid]
                entry["last_access"] = time.time()
                return entry["state"]

            # 2. 尝试磁盘加载
            state = self._load_from_disk(sid)
            if state is not None:
                self._sessions[sid] = {
                    "state": state,
                    "last_access": time.time(),
                    "dirty": False,
                }
                self._evict_lru_if_needed()
                return state

            # 3. 创建新会话
            state = create_fresh_state()
            self._sessions[sid] = {
                "state": state,
                "last_access": time.time(),
                "dirty": False,
            }
            self._evict_lru_if_needed()
            return state

    def reset(self, sid):
        """重置会话为空白状态，标记为脏以便写回磁盘。"""
        with self._lock:
            if sid in self._sessions:
                self._sessions[sid]["state"] = create_fresh_state()
                self._sessions[sid]["dirty"] = True
                self._sessions[sid]["last_access"] = time.time()

    def schedule_save(self, sid):
        """防抖保存：取消该 sid 的旧定时器，重新设一个 N 秒后的保存任务。"""
        # 取消已有定时器
        with self._lock:
            old_timer = self._save_timers.pop(sid, None)
        if old_timer is not None:
            old_timer.cancel()

        timer = threading.Timer(self._save_delay, self._do_save, args=[sid])
        timer.daemon = True
        with self._lock:
            self._save_timers[sid] = timer
        timer.start()

    def delete_session(self, sid):
        """删除指定会话（内存 + 磁盘文件）。"""
        with self._lock:
            # 取消定时器
            timer = self._save_timers.pop(sid, None)
            if timer is not None:
                timer.cancel()

            self._sessions.pop(sid, None)

        # 删除磁盘文件
        filepath = self._session_path(sid)
        for ext in ("", ".tmp"):
            path = filepath + ext
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def flush_all(self):
        """立即将所有脏会话写入磁盘。通常在进程退出前调用。"""
        with self._lock:
            for timer in self._save_timers.values():
                timer.cancel()
            self._save_timers.clear()

            for sid, entry in list(self._sessions.items()):
                self._save_to_disk(sid, entry)

    def shutdown(self):
        """关闭会话存储：取消所有定时器，flush_all。"""
        with self._lock:
            self._shutdown = True
        self.flush_all()

    # ------ 内部方法 ------

    def _session_path(self, sid):
        """获取某会话的磁盘文件路径。"""
        return os.path.join(self._sessions_dir, f"{sid}.json")

    def _do_save(self, sid):
        """定时器回调：将指定会话写入磁盘。"""
        with self._lock:
            self._save_timers.pop(sid, None)
            entry = self._sessions.get(sid)
        if entry is not None:
            self._save_to_disk(sid, entry)

    def _save_to_disk(self, sid, entry):
        """原子写入：先写 .tmp 再 os.replace()，防止进程中途被杀导致文件损坏。"""
        filepath = self._session_path(sid)
        tmp_path = filepath + ".tmp"

        data = {
            "version": 1,
            "created_at": entry.get("created_at", time.time()),
            "last_access": entry["last_access"],
            "state": entry["state"],
        }

        try:
            json_str = json.dumps(data, ensure_ascii=False, indent=2, default=str)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(json_str)
            os.replace(tmp_path, filepath)
            entry["dirty"] = False
        except OSError as e:
            import sys
            print(f"[SessionStore] 保存会话 {sid} 失败: {e}", file=sys.stderr)

    def _load_from_disk(self, sid):
        """从磁盘加载会话状态。失败或文件不存在返回 None。"""
        filepath = self._session_path(sid)
        if not os.path.isfile(filepath):
            return None

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError, OSError):
            return None

        if data.get("version", 0) < 1:
            return None

        return data.get("state", None)

    def _evict_lru_if_needed(self):
        """内存超上限时，按 last_access 淘汰最旧的会话（只从内存移除，不删磁盘文件）。

        调用方必须已持有 self._lock。
        """
        if len(self._sessions) <= self._max_memory:
            return

        # 按 last_access 升序排列，淘汰最旧的
        sorted_sids = sorted(
            self._sessions.keys(),
            key=lambda sid: self._sessions[sid]["last_access"],
        )
        to_remove = sorted_sids[:len(self._sessions) - self._max_memory]

        for sid in to_remove:
            entry = self._sessions[sid]
            # 脏会话先写回磁盘再淘汰
            if entry["dirty"]:
                self._save_to_disk(sid, entry)
            # 取消待处理的保存定时器
            timer = self._save_timers.pop(sid, None)
            if timer is not None:
                timer.cancel()
            del self._sessions[sid]


# ====== 模块级单例 ======

_session_store = None


def get_session_store():
    """获取全局 SessionStore 单例（惰性初始化）。"""
    global _session_store
    if _session_store is None:
        _session_store = SessionStore(
            sessions_dir=os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "sessions"
            ),
            max_memory=20,
            save_delay=5.0,
        )
    return _session_store
