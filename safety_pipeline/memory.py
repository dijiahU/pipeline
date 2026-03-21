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

    @staticmethod
    def _is_indexable_case(case):
        step = case.get("step", {}) or {}
        return bool(step.get("tool"))

    @staticmethod
    def _build_case_text(case):
        step = case.get("step", {}) or {}
        risk = get_case_risk_assessment(case)
        plan_memory = case.get("plan_memory", {}) or {}
        dialogue_snapshot = case.get("dialogue_snapshot", {}) or {}
        lines = [
            f"task: {case.get('task', '')}",
            f"task_summary: {summarize_result_for_memory(case.get('task', ''), limit=320)}",
            f"known_context: {' | '.join(dialogue_snapshot.get('known_context', [])[:6])}",
            f"authorization: {' | '.join(dialogue_snapshot.get('authorization_state', [])[:4])}",
            f"outcome: {case.get('outcome', '')}",
            f"decision: {case.get('decision', '')}",
            f"decision_reason: {case.get('decision_reason', '')}",
            f"tool: {step.get('tool', '')}",
            f"args: {json.dumps(step.get('args', {}), ensure_ascii=False, sort_keys=True)}",
            f"description: {step.get('description', '')}",
            f"risk: {risk.get('result', '')}",
            f"risk_reason: {risk.get('reasoning', '')}",
            f"plan_memory_summary: {plan_memory.get('summary', '')}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _build_query_text(task_query):
        return "\n".join(
            [
                "retrieval_scope: task_level",
                f"task_query: {task_query}",
            ]
        )

    @staticmethod
    def _build_meta_entry(case, text):
        step = case.get("step", {}) or {}
        return {
            "memory_id": case.get("memory_id", ""),
            "text": text,
            "tool": step.get("tool", ""),
            "description": step.get("description", ""),
            "decision": case.get("decision", ""),
            "decision_reason": case.get("decision_reason", ""),
            "outcome": case.get("outcome", ""),
        }

    def rebuild_from_experience(self):
        faiss = _import_faiss()
        cases = [
            c for c in self.experience_store.cases
            if isinstance(c, dict) and self._is_indexable_case(c)
        ]
        if not cases:
            self.metadata = []
            self.index = None
            self.save()
            return
        texts = [self._build_case_text(c) for c in cases]
        vectors = self._embed_texts(texts)
        dim = vectors.shape[1]
        self.index = faiss.IndexFlatIP(dim)
        self.index.add(vectors)
        self.metadata = [self._build_meta_entry(c, t) for c, t in zip(cases, texts)]
        self.save()

    def sync_with_experience(self):
        faiss = _import_faiss()

        cases = [
            c for c in self.experience_store.cases
            if isinstance(c, dict) and self._is_indexable_case(c)
        ]
        case_ids = {c.get("memory_id") for c in cases if c.get("memory_id")}
        meta_ids = {m.get("memory_id") for m in self.metadata}

        if meta_ids - case_ids:
            self.rebuild_from_experience()
            return

        case_map = {c.get("memory_id"): c for c in cases if c.get("memory_id")}
        for entry in self.metadata:
            mid = entry.get("memory_id")
            if mid and mid in case_map:
                current_text = self._build_case_text(case_map[mid])
                if current_text != entry.get("text", ""):
                    self.rebuild_from_experience()
                    return

        new_cases = [c for c in cases if c.get("memory_id") and c["memory_id"] not in meta_ids]
        if not new_cases:
            return

        texts = [self._build_case_text(c) for c in new_cases]
        vectors = self._embed_texts(texts)

        if self.index is None:
            dim = vectors.shape[1]
            self.index = faiss.IndexFlatIP(dim)

        self.index.add(vectors)
        self.metadata.extend(self._build_meta_entry(c, t) for c, t in zip(new_cases, texts))
        self.save()

    def ensure_synced(self):
        if not self._synced:
            self.sync_with_experience()
            self._synced = True

    def search(self, task_query, limit=PLAN_MEMORY_TOP_K):
        self.ensure_synced()
        exp_count = sum(
            1 for c in self.experience_store.cases
            if isinstance(c, dict) and self._is_indexable_case(c)
        )
        if exp_count != len(self.metadata):
            self.sync_with_experience()

        if self.index is None or self.index.ntotal == 0:
            return []

        query_text = self._build_query_text(task_query)
        query_vec = self._embed_text(query_text).reshape(1, -1)

        k = min(limit, self.index.ntotal)
        scores, indices = self.index.search(query_vec, k)

        case_map = {
            c.get("memory_id"): c
            for c in self.experience_store.cases
            if isinstance(c, dict) and c.get("memory_id")
        }

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.metadata):
                continue
            memory_id = self.metadata[idx].get("memory_id")
            if memory_id not in case_map:
                continue
            results.append({
                "score": round(float(score), 4),
                "case": case_map[memory_id],
            })
        return results


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
    global plan_memory_store
    if plan_memory_store is None:
        plan_memory_store = PlanMemoryVectorStore(
            PLAN_MEMORY_FAISS_PATH,
            PLAN_MEMORY_META_PATH,
            experience_memory,
        )
    return plan_memory_store


def memory_for_plan(task_query):
    recalled = get_plan_memory_store().search(task_query, limit=PLAN_MEMORY_TOP_K)
    cases = []
    evidence = []

    for item in recalled:
        case = item["case"]
        step = case.get("step", {}) or {}
        risk = get_case_risk_assessment(case)
        reasoning = case.get("decision_reason", "") or risk.get("reasoning", "")
        case_view = {
            "memory_id": case.get("memory_id", ""),
            "score": item["score"],
            "task": case.get("task", ""),
            "tool": step.get("tool", ""),
            "args": step.get("args", {}),
            "description": step.get("description", ""),
            "decision": case.get("decision", ""),
            "outcome": case.get("outcome", ""),
            "reason": reasoning,
        }
        cases.append(case_view)
        if reasoning and reasoning not in evidence:
            evidence.append(reasoning)
        outcome = case.get("outcome", "")
        if outcome:
            outcome_evidence = f"历史相似案例常见 outcome: {outcome}"
            if outcome_evidence not in evidence:
                evidence.append(outcome_evidence)

    if not cases:
        summary = "向量库中没有召回到相关历史任务。"
    else:
        outcome_counts = {}
        for case in cases:
            outcome = case.get("outcome", "unknown") or "unknown"
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        top_score = cases[0]["score"]
        summary = (
            f"任务级向量检索召回 {len(cases)} 条相关历史任务，最高相似度 {top_score:.4f}，"
            f"主要 outcome 分布: {outcome_counts}"
        )

    return {
        "task_query": task_query,
        "cases": cases,
        "summary": summary,
        "evidence": evidence[:6],
        "retrieval_method": "local_faiss_embedding_v1",
        "retrieval_scope": "task_level",
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


def memory_for_tool(tool_name, args):
    safe_case = tool_memory.get_safe_case(tool_name, args)
    return {
        "hit": safe_case is not None,
        "safe_case": sanitize_safe_case_for_observation(safe_case),
        "summary": "命中完全相同调用的安全缓存。"
        if safe_case
        else "没有找到完全相同调用的安全缓存。",
    }


def sanitize_tool_memory_result(tool_memory_result):
    tool_memory_result = tool_memory_result or {}
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
    plan_memory_result = dict(plan_memory_result or {})
    raw_cases = list(plan_memory_result.get("cases", []) or [])
    memory_id_to_task = {
        item.get("memory_id"): item.get("task", "")
        for item in experience_memory.cases
        if isinstance(item, dict) and item.get("memory_id")
    }

    filtered_cases = []
    for case_view in raw_cases:
        tool_name = (case_view.get("tool") or "").strip()
        if not tool_name:
            continue
        hydrated = dict(case_view)
        if not hydrated.get("task"):
            hydrated["task"] = memory_id_to_task.get(hydrated.get("memory_id"), "")
        filtered_cases.append(hydrated)

    evidence = []
    if not filtered_cases:
        summary = "向量库中没有召回到相关历史任务。"
    else:
        outcome_counts = {}
        for case in filtered_cases:
            outcome = case.get("outcome", "unknown") or "unknown"
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            reason = case.get("reason", "")
            if reason and reason not in evidence:
                evidence.append(reason)
            outcome_evidence = f"历史相似案例常见 outcome: {outcome}"
            if outcome_evidence not in evidence:
                evidence.append(outcome_evidence)
        top_score = filtered_cases[0].get("score", 0)
        summary = (
            f"任务级向量检索召回 {len(filtered_cases)} 条相关历史任务，最高相似度 {top_score:.4f}，"
            f"主要 outcome 分布: {outcome_counts}"
        )

    plan_memory_result.pop("task_context", None)
    plan_memory_result.pop("current_step", None)
    plan_memory_result["cases"] = filtered_cases
    plan_memory_result["task_query"] = plan_memory_result.get("task_query") or build_task_memory_query_from_case(current_case)
    plan_memory_result["retrieval_scope"] = "task_level"
    plan_memory_result["summary"] = summary
    plan_memory_result["evidence"] = evidence[:6]
    return plan_memory_result
