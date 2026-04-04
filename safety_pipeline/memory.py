import json
import os
import uuid

from .service_registry import get_service_spec, list_all_service_specs
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


def _normalize_service_lookup_text(text):
    return " ".join(str(text or "").strip().lower().replace(".", " ").split())


def _infer_service_context_from_text(text):
    normalized = _normalize_service_lookup_text(text)
    if not normalized:
        return {}
    for spec in list_all_service_specs():
        candidates = {
            _normalize_service_lookup_text(spec.service_id),
            _normalize_service_lookup_text(spec.display_name),
        }
        if any(candidate and candidate in normalized for candidate in candidates):
            return {
                "service_id": spec.service_id,
                "environment": spec.default_backend or spec.service_id,
            }
    return {}


def extract_case_service_context(case):
    case = case or {}
    context = {}
    service_id = str(case.get("service_id", "") or "").strip()
    environment = str(case.get("environment", "") or "").strip()
    if service_id:
        context["service_id"] = service_id
    if environment:
        context["environment"] = environment

    snapshot = case.get("dialogue_snapshot", {}) or {}
    snapshot_service_context = snapshot.get("service_context", {}) or {}
    if not context.get("service_id"):
        snapshot_service_id = str(snapshot_service_context.get("service_id", "") or "").strip()
        if snapshot_service_id:
            context["service_id"] = snapshot_service_id
    if not context.get("environment"):
        snapshot_environment = str(snapshot_service_context.get("environment", "") or "").strip()
        if snapshot_environment:
            context["environment"] = snapshot_environment

    inferred = {}
    if not context.get("service_id") or not context.get("environment"):
        inferred = _infer_service_context_from_text(case.get("task", ""))
        if not inferred:
            for item in snapshot.get("dialogue_history", []) or []:
                inferred = _infer_service_context_from_text(item.get("content", ""))
                if inferred:
                    break

    if not context.get("service_id") and inferred.get("service_id"):
        context["service_id"] = inferred["service_id"]
    if not context.get("environment") and inferred.get("environment"):
        context["environment"] = inferred["environment"]

    if context.get("service_id") and not context.get("environment"):
        spec = get_service_spec(context["service_id"])
        if spec is not None:
            context["environment"] = spec.default_backend or spec.service_id
    if context.get("environment") and not context.get("service_id"):
        spec = get_service_spec(context["environment"])
        if spec is not None:
            context["service_id"] = spec.service_id

    return context


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
            print(f"[memory] experience memory file is corrupted and was ignored: {self.storage_path}")
            self.cases = []
            return
        self.cases = data if isinstance(data, list) else []
        for case in self.cases:
            if not isinstance(case, dict):
                continue
            if not case.get("memory_id"):
                case["memory_id"] = f"case-{uuid.uuid4().hex}"
                dirty = True
            context = extract_case_service_context(case)
            if context.get("service_id") and case.get("service_id") != context["service_id"]:
                case["service_id"] = context["service_id"]
                dirty = True
            if context.get("environment") and case.get("environment") != context["environment"]:
                case["environment"] = context["environment"]
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
                print(f"[memory] FAISS index is corrupted and will be rebuilt: {self.faiss_path}")
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

    # ---- session grouping ----

    @staticmethod
    def _group_sessions(cases):
        """Group experience cases by session (consecutive steps from the same task)."""
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
        """Use the first case memory_id as the unique session identifier."""
        return session[0].get("memory_id", "") if session else ""

    @staticmethod
    def _extract_real_tool_steps(session):
        """Extract the real-tool execution trace from a session."""
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
        """Return the final session status."""
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
        """Build the vector-index text for a full session trajectory."""
        task = session[0].get("task", "") if session else ""
        real_steps = self._extract_real_tool_steps(session)
        tool_chain = " -> ".join(
            f"{s['tool']}({json.dumps(s['args'], ensure_ascii=False, sort_keys=True)})"
            for s in real_steps
        ) or "no real tool calls"
        final_status = self._session_final_status(session)
        context = self._extract_session_service_context(session)
        lines = [
            f"task: {task}",
            f"service_id: {context.get('service_id', '')}",
            f"environment: {context.get('environment', '')}",
            f"tool_chain: {tool_chain}",
            f"step_count: {len(real_steps)}",
            f"final_status: {final_status}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _build_query_text(task_query):
        return f"task: {task_query}"

    def _build_session_meta(self, session, text):
        context = self._extract_session_service_context(session)
        return {
            "session_id": self._session_id(session),
            "case_ids": [c.get("memory_id", "") for c in session],
            "text": text,
            "task": session[0].get("task", "") if session else "",
            "final_status": self._session_final_status(session),
            "service_id": context.get("service_id", ""),
            "environment": context.get("environment", ""),
        }

    @staticmethod
    def _extract_session_service_context(session):
        for case in session or []:
            context = extract_case_service_context(case)
            if context.get("service_id") or context.get("environment"):
                return context
        return {}

    @staticmethod
    def _meta_matches_filters(meta, filters):
        filters = {key: value for key, value in (filters or {}).items() if value}
        if not filters:
            return True
        for key, expected in filters.items():
            actual = str(meta.get(key, "") or "").strip()
            if not actual or actual != str(expected).strip():
                return False
        return True

    def _get_sessions(self):
        sessions = self._group_sessions(self.experience_store.cases)
        # Keep only sessions that contain at least one real tool call.
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
            # Rebuild fully on any change; session-level churn is infrequent.
            self.rebuild_from_experience()
            return

        # Check whether any indexed text changed.
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

    def search(self, task_query, limit=PLAN_MEMORY_TOP_K, filters=None):
        self.ensure_synced()

        sessions = self._get_sessions()
        if len(sessions) != len(self.metadata):
            self.sync_with_experience()

        if self.index is None or self.index.ntotal == 0:
            return []

        query_text = self._build_query_text(task_query)
        query_vec = self._embed_text(query_text).reshape(1, -1)

        k = self.index.ntotal if filters else min(limit, self.index.ntotal)
        scores, indices = self.index.search(query_vec, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.metadata):
                continue
            meta = self.metadata[idx]
            if not self._meta_matches_filters(meta, filters):
                continue
            session_id = meta.get("session_id", "")
            # Find all cases belonging to the matched session.
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
                "meta": meta,
            })
            if len(results) >= limit:
                break
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

    def search(self, task_query, limit=PLAN_MEMORY_TOP_K, filters=None):
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
            print(f"[memory] tool memory file is corrupted and was ignored: {self.storage_path}")
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
        """Return the most recent top_k safe records for a tool."""
        matches = [
            case for case in self.safe_cases.values()
            if case.get("tool") == tool_name
        ]
        # Take the latest top_k entries in insertion order.
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


def _build_trajectory_view(session, score, meta=None):
    """Convert a session into a trajectory view that only shows real tool calls."""
    store = get_plan_memory_store()
    task = session[0].get("task", "") if session else ""
    real_steps = store._extract_real_tool_steps(session)
    final_status = store._session_final_status(session)
    service_context = dict(meta or {})
    if not service_context.get("service_id") and not service_context.get("environment"):
        service_context = store._extract_session_service_context(session)
    return {
        "score": score,
        "task": task,
        "final_status": final_status,
        "service_id": service_context.get("service_id", ""),
        "environment": service_context.get("environment", ""),
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


def memory_for_plan(task_query, service_id=None, environment=None):
    filters = {}
    if service_id:
        filters["service_id"] = service_id
    if environment:
        filters["environment"] = environment
    recalled = get_plan_memory_store().search(task_query, limit=PLAN_MEMORY_TOP_K, filters=filters or None)
    trajectories = []

    for item in recalled:
        session = item["session"]
        trajectory = _build_trajectory_view(session, item["score"], meta=item.get("meta"))
        trajectories.append(trajectory)

    if not trajectories:
        if _plan_memory_disabled_reason:
            summary = f"Plan memory fell back to empty recall: {_plan_memory_disabled_reason}"
        else:
            summary = "No relevant historical trajectories were recalled from the vector store."
    else:
        top_score = trajectories[0]["score"]
        status_counts = {}
        for t in trajectories:
            s = t["final_status"]
            status_counts[s] = status_counts.get(s, 0) + 1
        summary = (
            f"Trajectory-level vector retrieval recalled {len(trajectories)} similar historical tasks, "
            f"top similarity {top_score:.4f}, final status distribution: {status_counts}"
        )

    return {
        "task_query": task_query,
        "trajectories": trajectories,
        "summary": summary,
        "retrieval_method": "disabled" if _plan_memory_disabled_reason else "local_faiss_embedding_v1",
        "retrieval_scope": "trajectory_level",
        "service_filtered": bool(filters),
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
    """Return the two most recent safe records for a tool."""
    cases = tool_memory.get_safe_cases_by_tool(tool_name, top_k=2)
    return {
        "hit": len(cases) > 0,
        "safe_cases": [sanitize_safe_case_for_observation(c) for c in cases],
        "summary": f"Found {len(cases)} safe call record(s) for {tool_name}."
        if cases
        else f"No safe call records found for {tool_name}.",
    }


def sanitize_tool_memory_result(tool_memory_result):
    tool_memory_result = tool_memory_result or {}
    # Support both the new format (safe_cases list) and the old format (single safe_case).
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
    lines = [f"Current task: {task_text}"]
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
        lines.append(f"Known context: {' | '.join(known_context[:6])}")
    if authorization:
        lines.append(f"Authorization state: {' | '.join(authorization[:4])}")
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
    """Normalize plan memory results while remaining compatible with older data."""
    plan_memory_result = dict(plan_memory_result or {})
    # Keep both the new trajectories field and the legacy cases field if present.
    plan_memory_result.pop("task_context", None)
    plan_memory_result.pop("current_step", None)
    if not plan_memory_result.get("task_query"):
        plan_memory_result["task_query"] = build_task_memory_query_from_case(current_case)
    if not plan_memory_result.get("summary"):
        plan_memory_result["summary"] = "No relevant historical trajectories were recalled from the vector store."
    return plan_memory_result
