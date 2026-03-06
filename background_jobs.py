"""
后台任务管理器。

模块级 _jobs 字典在整个 Streamlit 进程生命周期内持久存在，
即使用户切换页面或关闭浏览器，后台线程仍在运行并持续更新状态。
"""
import threading
import uuid
from typing import Dict, Any, Optional

_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def create_job(repo: str) -> str:
    job_id = uuid.uuid4().hex[:8]
    with _lock:
        _jobs[job_id] = {
            "repo": repo,
            "status": "running",    # running | complete | error
            "phase": "starting",    # starting | repo_info | contributors | stats | merging | enriching | saving
            "done": 0,
            "total": 0,
            "contrib_count": 0,
            "skip_count": 0,
            "rl_status": "normal",
            "rl_remaining": 5000,
            "rl_wait_s": 0,
            "details": None,
            "error": None,
        }
    return job_id


def update_job(job_id: str, **kwargs):
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


def get_job(job_id: str) -> Optional[Dict]:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def get_active_job_for_repo(repo: str) -> Optional[str]:
    """返回当前正在运行的同仓库任务 ID（若有），用于断线重连。"""
    with _lock:
        for jid, j in _jobs.items():
            if j["repo"] == repo and j["status"] == "running":
                return jid
    return None


def finish_job(job_id: str, error: str = None):
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "error" if error else "complete"
            _jobs[job_id]["phase"] = "error" if error else "complete"
            _jobs[job_id]["error"] = error


def cleanup_job(job_id: str):
    with _lock:
        _jobs.pop(job_id, None)
