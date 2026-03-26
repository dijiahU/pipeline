import json
import os
import uuid

from .settings import (
    EXPERIENCE_MEMORY_PATH,
    LOCAL_EMBEDDING_MODEL,
    PLAN_MEMORY_FAISS_PATH,
    PLAN_MEMORY_META_PATH,
    PLAN_MEMORY_TOP_K,
    TOOL_MEMORY_PATH,
)
from .state import get_case_risk_assessment, summarize_result_for_memory


def tool_signature(tool_name, args):
    return f"{tool_name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"


class ExperienceMemory:
    def __init__(self, storage_path):
        self.storage_path = storage_path
        self.cases = []
        self.load()

    def load(self):
        dirty = False
        try:
            with open(self.storage_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            self.cases = []
            return
        except json.JSONDecodeError:
            print(f"[memory] experience memory 文件损坏，已忽略: {self.storage_path}")
            self.cases = []
            return
        self.cases = data if isinstance(data, list) else []
        for case in self.cases:
            if not isinstance(case, dict):
                continue
            if not case.get("memory_id"):
                case["memory_id"] = f"case-{uuid.uuid4().hex}"
                dirty = True
        if dirty:
            self.save()

    def save(self):
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        with open(self.storage_path, "w", encoding="utf-8") as fh:
            json.dump(self.cases, fh, ensure_ascii=False, indent=2)

    def store_case(self, case):
        if not case.get("memory_id"):
            case["memory_id"] = f"case-{uuid.uuid4().hex}"
        self.cases.append(case)
        self.save()


_local_embedding_model = None
plan_memory_store = None
_plan_memory_disabled_reason = None


def get_local_embedding_model():
    global _local_embedding_model
    if _local_embedding_model is not None:
        return _local_embedding_model
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError(
            "sentence-transformers is required for plan memory. "
            "Install with: pip install sentence-transformers"
        )
    print(f"[plan_memory] loading local embedding model: {LOCAL_EMBEDDING_MODEL}")
    import logging
    import warnings
    logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*position_ids.*")
        warnings.filterwarnings("ignore", message=".*unauthenticated.*")
        _local_embedding_model = SentenceTransformer(LOCAL_EMBEDDING_MODEL)
    return _local_embedding_model


def _import_faiss():
    try:
        import faiss
        return faiss
    except ImportError:
        raise RuntimeError(
            "faiss-cpu is required for plan memory. "
            "Install with: pip install faiss-cpu"
        )


class PlanMemoryVectorStore:
    def __init__(self, faiss_path, meta_path, experience_store):
        self.faiss_path = faiss_path
        self.meta_path = meta_path
        self.experience_store = experience_store
        self.metadata = []
        self.index = None
        self._synced = False
        self.load()

    def load(self):
        faiss = _import_faiss()
        try:
            with open(self.meta_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.metadata = data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            self.metadata = []

        if os.path.exists(self.faiss_path) and self.metadata:
            try:
                self.index = faiss.read_index(self.faiss_path)
            except Exception:
                print(f"[memory] FAISS index 损坏，将重建: {self.faiss_path}")
                self.index = None
                self.metadata = []
        else:
            self.index = None

    def save(self):
        faiss = _import_faiss()
        os.makedirs(os.path.dirname(self.faiss_path), exist_ok=True)
        if self.index is not None and self.index.ntotal > 0:
            faiss.write_index(self.index, self.faiss_path)
        with open(self.meta_path, "w", encoding="utf-8") as fh:
            json.dump(self.metadata, fh, ensure_ascii=False, indent=2)

    def _embed_text(self, text):
        import numpy as np

        model = get_local_embedding_model()
        vec = model.encode(text, normalize_embeddings=True)
        return np.array(vec, dtype=np.float32)

    def _embed_texts(self, texts):
        import numpy as np

        model = get_local_embedding_model()
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.array(vecs, dtype=np.float32)

    # ---- session 分组 ----

    @staticmethod
    def _group_sessions(cases):
        """把 experience cases 按 session（同一任务的连续 step）分组"""
        sessions = []
        current = []
        prev_step_index = None
        for case in cases:
            if not isinstance(case, dict):
                continue
            step_index = case.get("step_index", 0)
            if current and prev_step_index is not None and step_index <= prev_step_index:
                sessions.append(current)
                current = []
            current.append(case)
            prev_step_index = step_index
        if current:
            sessions.append(current)
        return sessions

    @staticmethod
    def _session_id(session):
        """用首条 case 的 memory_id 作为 session 唯一标识"""
        return session[0].get("memory_id", "") if session else ""

    @staticmethod
    def _extract_real_tool_steps(session):
        """从 session 中提取真实工具执行轨迹（只看 real tool，不看 flow tool）"""
        steps = []
        for case in session:
            step = case.get("step") or {}
            tool = step.get("tool", "")
            if not tool:
                continue
            steps.append({
                "tool": tool,
                "args": step.get("args") or {},
                "description": step.get("description", ""),
                "decision": case.get("decision", ""),
                "outcome": case.get("outcome", ""),
            })
        return steps

    @staticmethod
    def _session_final_status(session):
        """session 最终状态"""
        last = session[-1] if session else {}
        decision = last.get("decision", "")
        outcome = last.get("outcome", "")
        if outcome in {"completion_done", "tool_memory_hit", "try_safe_then_executed"}:
            return "done"
        if decision in {"refuse", "terminate"}:
            return decision
        if decision == "ask_human":
            return "ask_human"
        return outcome or decision or "unknown"

    def _build_session_text(self, session):
        """为整条轨迹构建向量索引文本"""
        task = session[0].get("task", "") if session else ""
        real_steps = self._extract_real_tool_steps(session)
        tool_chain = " → ".join(
            f"{s['tool']}({json.dumps(s['args'], ensure_ascii=False, sort_keys=True)})"
            for s in real_steps
        ) or "无真实工具调用"
        final_status = self._session_final_status(session)
        lines = [
            f"task: {task}",
            f"tool_chain: {tool_chain}",
            f"step_count: {len(real_steps)}",
            f"final_status: {final_status}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _build_query_text(task_query):
        return f"task: {task_query}"

    def _build_session_meta(self, session, text):
        return {
            "session_id": self._session_id(session),
            "case_ids": [c.get("memory_id", "") for c in session],
            "text": text,
            "task": session[0].get("task", "") if session else "",
            "final_status": self._session_final_status(session),
        }

    def _get_sessions(self):
        sessions = self._group_sessions(self.experience_store.cases)
        # 只保留至少有一个真实工具调用的 session
        return [s for s in sessions if self._extract_real_tool_steps(s)]

    def rebuild_from_experience(self):
        faiss = _import_faiss()
        sessions = self._get_sessions()
        if not sessions:
            self.metadata = []
            self.index = None
            self.save()
            return
        texts = [self._build_session_text(s) for s in sessions]
        vectors = self._embed_texts(texts)
        dim = vectors.shape[1]
        self.index = faiss.IndexFlatIP(dim)
        self.index.add(vectors)
        self.metadata = [self._build_session_meta(s, t) for s, t in zip(sessions, texts)]
        self.save()

    def sync_with_experience(self):
        sessions = self._get_sessions()
        session_ids = {self._session_id(s) for s in sessions}
        meta_ids = {m.get("session_id") for m in self.metadata}

        if meta_ids != session_ids:
            # 任何变化都全量重建（session 粒度变化不频繁）
            self.rebuild_from_experience()
            return

        # 检查内容是否有变化
        meta_text_map = {m.get("session_id"): m.get("text", "") for m in self.metadata}
        session_map = {self._session_id(s): s for s in sessions}
        for sid, session in session_map.items():
            if self._build_session_text(session) != meta_text_map.get(sid, ""):
                self.rebuild_from_experience()
                return

    def ensure_synced(self):
        if not self._synced:
            self.sync_with_experience()
            self._synced = True

    def search(self, task_query, limit=PLAN_MEMORY_TOP_K):
        self.ensure_synced()

        sessions = self._get_sessions()
        if len(sessions) != len(self.metadata):
            self.sync_with_experience()

        if self.index is None or self.index.ntotal == 0:
            return []

        query_text = self._build_query_text(task_query)
        query_vec = self._embed_text(query_text).reshape(1, -1)

        k = min(limit, self.index.ntotal)
        scores, indices = self.index.search(query_vec, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.metadata):
                continue
            meta = self.metadata[idx]
            session_id = meta.get("session_id", "")
            # 找到对应 session 的所有 cases
            matched_session = None
            for s in sessions:
                if self._session_id(s) == session_id:
                    matched_session = s
                    break
            if not matched_session:
                continue
            results.append({
                "score": round(float(score), 4),
                "session": matched_session,
            })
        return results


class DisabledPlanMemoryStore:
    def __init__(self, reason):
        self.reason = reason
        self.metadata = []
        self.index = None

    def sync_with_experience(self):
        return

    def ensure_synced(self):
        return

    def search(self, task_query, limit=PLAN_MEMORY_TOP_K):
        return []


class ToolMemory:
    def __init__(self, storage_path):
        self.storage_path = storage_path
        self.safe_cases = {}
        self.load()

    def load(self):
        try:
            with open(self.storage_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            self.safe_cases = {}
            return
        except json.JSONDecodeError:
            print(f"[memory] tool memory 文件损坏，已忽略: {self.storage_path}")
            self.safe_cases = {}
            return
        self.safe_cases = data if isinstance(data, dict) else {}

    def save(self):
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        with open(self.storage_path, "w", encoding="utf-8") as fh:
            json.dump(self.safe_cases, fh, ensure_ascii=False, indent=2)

    def get_safe_case(self, tool_name, args):
        return self.safe_cases.get(tool_signature(tool_name, args))

    def get_safe_cases_by_tool(self, tool_name, top_k=2):
        """按工具名检索安全记录，返回最近 top_k 条"""
        matches = [
            case for case in self.safe_cases.values()
            if case.get("tool") == tool_name
        ]
        # 按存入顺序取最后 top_k 条（字典保持插入顺序）
        return matches[-top_k:] if matches else []

    def store_safe_case(self, tool_name, args, exec_result, safety_reason):
        self.safe_cases[tool_signature(tool_name, args)] = {
            "tool": tool_name,
            "args": args,
            "exec_result": exec_result,
            "exec_result_summary": summarize_result_for_memory(exec_result),
            "state": "safe",
            "safety_reason": safety_reason,
        }
        self.save()


experience_memory = ExperienceMemory(EXPERIENCE_MEMORY_PATH)
tool_memory = ToolMemory(TOOL_MEMORY_PATH)


def get_plan_memory_store():
    global plan_memory_store, _plan_memory_disabled_reason
    if plan_memory_store is None:
        try:
            plan_memory_store = PlanMemoryVectorStore(
                PLAN_MEMORY_FAISS_PATH,
                PLAN_MEMORY_META_PATH,
                experience_memory,
            )
            _plan_memory_disabled_reason = None
        except RuntimeError as exc:
            _plan_memory_disabled_reason = str(exc)
            print(f"[plan_memory] disabled: {_plan_memory_disabled_reason}")
            plan_memory_store = DisabledPlanMemoryStore(_plan_memory_disabled_reason)
    return plan_memory_store


def _build_trajectory_view(session, score):
    """把一个 session 转成轨迹视图，只展示真实工具调用链"""
    store = get_plan_memory_store()
    task = session[0].get("task", "") if session else ""
    real_steps = store._extract_real_tool_steps(session)
    final_status = store._session_final_status(session)
    return {
        "score": score,
        "task": task,
        "final_status": final_status,
        "tool_chain": [
            {
                "tool": s["tool"],
                "args": s["args"],
                "description": s["description"],
                "outcome": s["outcome"],
            }
            for s in real_steps
        ],
    }


def memory_for_plan(task_query):
    recalled = get_plan_memory_store().search(task_query, limit=PLAN_MEMORY_TOP_K)
    trajectories = []

    for item in recalled:
        session = item["session"]
        trajectory = _build_trajectory_view(session, item["score"])
        trajectories.append(trajectory)

    if not trajectories:
        if _plan_memory_disabled_reason:
            summary = f"计划记忆已降级为空召回：{_plan_memory_disabled_reason}"
        else:
            summary = "向量库中没有召回到相关历史轨迹。"
    else:
        top_score = trajectories[0]["score"]
        status_counts = {}
        for t in trajectories:
            s = t["final_status"]
            status_counts[s] = status_counts.get(s, 0) + 1
        summary = (
            f"轨迹级向量检索召回 {len(trajectories)} 条相似历史任务，"
            f"最高相似度 {top_score:.4f}，最终状态分布: {status_counts}"
        )

    return {
        "task_query": task_query,
        "trajectories": trajectories,
        "summary": summary,
        "retrieval_method": "disabled" if _plan_memory_disabled_reason else "local_faiss_embedding_v1",
        "retrieval_scope": "trajectory_level",
    }


def sanitize_safe_case_for_observation(safe_case):
    if not safe_case:
        return None
    return {
        "tool": safe_case.get("tool", ""),
        "args": safe_case.get("args", {}) or {},
        "state": safe_case.get("state", ""),
        "safety_reason": safe_case.get("safety_reason", ""),
        "exec_result_summary": safe_case.get("exec_result_summary")
        or summarize_result_for_memory(safe_case.get("exec_result")),
    }


def memory_for_tool(tool_name):
    """按工具名检索安全记录，返回最近 2 条"""
    cases = tool_memory.get_safe_cases_by_tool(tool_name, top_k=2)
    return {
        "hit": len(cases) > 0,
        "safe_cases": [sanitize_safe_case_for_observation(c) for c in cases],
        "summary": f"找到 {len(cases)} 条 {tool_name} 的安全调用记录。"
        if cases
        else f"没有找到 {tool_name} 的安全调用记录。",
    }


def sanitize_tool_memory_result(tool_memory_result):
    tool_memory_result = tool_memory_result or {}
    # 兼容新格式（safe_cases 列表）和旧格式（safe_case 单条）
    if "safe_cases" in tool_memory_result:
        return {
            "hit": bool(tool_memory_result.get("hit")),
            "safe_cases": [
                sanitize_safe_case_for_observation(c)
                for c in (tool_memory_result.get("safe_cases") or [])
            ],
            "summary": tool_memory_result.get("summary", ""),
        }
    return {
        "hit": bool(tool_memory_result.get("hit")),
        "safe_case": sanitize_safe_case_for_observation(tool_memory_result.get("safe_case")),
        "summary": tool_memory_result.get("summary", ""),
    }


def compose_task_query(task_text, known_context=None, authorization=None):
    lines = [f"当前任务: {task_text}"]
    known_context = [
        summarize_result_for_memory(item, limit=120)
        for item in (known_context or [])
        if str(item).strip()
    ]
    authorization = [
        summarize_result_for_memory(item, limit=80)
        for item in (authorization or [])
        if str(item).strip()
    ]
    if known_context:
        lines.append(f"已知上下文: {' | '.join(known_context[:6])}")
    if authorization:
        lines.append(f"授权状态: {' | '.join(authorization[:4])}")
    return "\n".join(lines)


def build_task_memory_query_from_case(case):
    case = case or {}
    snapshot = case.get("dialogue_snapshot", {}) or {}
    return compose_task_query(
        case.get("task", ""),
        snapshot.get("known_context", []),
        snapshot.get("authorization_state", []),
    )


def sanitize_plan_memory_result(plan_memory_result, current_case=None):
    """兼容旧数据的 plan memory 结果清洗"""
    plan_memory_result = dict(plan_memory_result or {})
    # 新格式用 trajectories，旧格式用 cases，都保留
    plan_memory_result.pop("task_context", None)
    plan_memory_result.pop("current_step", None)
    if not plan_memory_result.get("task_query"):
        plan_memory_result["task_query"] = build_task_memory_query_from_case(current_case)
    if not plan_memory_result.get("summary"):
        plan_memory_result["summary"] = "向量库中没有召回到相关历史轨迹。"
    return plan_memory_result
