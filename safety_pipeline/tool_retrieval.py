import re
from collections import Counter

from .memory import get_local_embedding_model


def _normalize_text(text):
    return " ".join(str(text or "").replace("\n", " ").split()).strip()


def _tokenize(text):
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", _normalize_text(text).lower())


class ToolIndex:
    """基于工具元数据的轻量检索索引。"""

    def __init__(self, tool_summary, tool_schemas=None):
        self.tool_summary = list(tool_summary or [])
        self.tool_schema_map = self._build_schema_map(tool_schemas)
        self.documents = [self._build_document(tool) for tool in self.tool_summary]
        self.document_tokens = [Counter(_tokenize(doc)) for doc in self.documents]
        self.group_summary = self._build_group_summary()
        self.tool_names = [tool.get("name", "") for tool in self.tool_summary]
        self.index = None
        self.retrieval_method = "keyword_overlap_v1"
        self.disabled_reason = ""
        self.build_index()

    @staticmethod
    def _build_schema_map(tool_schemas):
        schema_map = {}
        for schema in tool_schemas or []:
            func = (schema or {}).get("function") or {}
            name = func.get("name", "")
            if name:
                schema_map[name] = schema
        return schema_map

    def _build_group_summary(self):
        groups = {}
        for tool in self.tool_summary:
            group_name = tool.get("group", "general") or "general"
            if group_name not in groups:
                groups[group_name] = {
                    "group": group_name,
                    "desc": tool.get("group_description", ""),
                    "count": 0,
                }
            groups[group_name]["count"] += 1
        return list(groups.values())

    def _build_document(self, tool):
        name = tool.get("name", "")
        schema = self.tool_schema_map.get(name, {})
        func = schema.get("function") or {}
        parts = [
            name,
            name.replace("_", " "),
            tool.get("group", ""),
            tool.get("group_description", ""),
            tool.get("short_description", ""),
            tool.get("description", ""),
            func.get("description", ""),
        ]
        return " | ".join(part for part in (_normalize_text(item) for item in parts) if part)

    def _embed_text(self, text):
        import numpy as np

        model = get_local_embedding_model()
        vector = model.encode(_normalize_text(text), normalize_embeddings=True)
        return np.array(vector, dtype=np.float32)

    def _embed_texts(self, texts):
        import numpy as np

        model = get_local_embedding_model()
        vectors = model.encode(
            [_normalize_text(text) for text in texts],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.array(vectors, dtype=np.float32)

    def build_index(self):
        if not self.documents:
            return
        try:
            import faiss

            embeddings = self._embed_texts(self.documents)
            if len(embeddings) == 0:
                return
            dim = embeddings.shape[1]
            self.index = faiss.IndexFlatIP(dim)
            self.index.add(embeddings)
            self.retrieval_method = "faiss_embedding_v1"
            self.disabled_reason = ""
        except Exception as exc:
            self.index = None
            self.retrieval_method = "keyword_overlap_v1"
            self.disabled_reason = f"{type(exc).__name__}: {exc}"

    def get_tool_groups_summary(self):
        return list(self.group_summary)

    def _format_result(self, tool, score):
        return {
            "name": tool.get("name", ""),
            "desc": tool.get("short_description") or tool.get("description", ""),
            "group": tool.get("group", "general") or "general",
            "is_write": bool(tool.get("is_write")),
            "score": round(float(score), 4),
        }

    def _retrieve_vector(self, query, top_k):
        query_vector = self._embed_text(query).reshape(1, -1)
        limit = min(int(top_k), self.index.ntotal)
        scores, indices = self.index.search(query_vector, limit)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.tool_summary):
                continue
            results.append(self._format_result(self.tool_summary[idx], score))
        return results

    def _retrieve_keyword(self, query, top_k):
        query_text = _normalize_text(query).lower()
        query_tokens = Counter(_tokenize(query_text))
        if not query_tokens:
            return []

        scored = []
        for idx, tool in enumerate(self.tool_summary):
            score = 0.0
            name = str(tool.get("name", "")).lower()
            group = str(tool.get("group", "")).lower()
            if name and name in query_text:
                score += 6.0
            if group and group.replace("_", " ") in query_text:
                score += 2.5
            token_counts = self.document_tokens[idx]
            overlap = sum(
                min(query_tokens[token], token_counts.get(token, 0))
                for token in query_tokens
            )
            if overlap:
                score += float(overlap)
            if score > 0:
                scored.append((score, idx, tool))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            self._format_result(tool, score)
            for score, _, tool in scored[:top_k]
        ]

    def _default_candidates(self, top_k):
        if top_k <= 0:
            return []
        groups = {}
        ordered_groups = []
        for tool in self.tool_summary:
            group_name = tool.get("group", "general") or "general"
            if group_name not in groups:
                groups[group_name] = []
                ordered_groups.append(group_name)
            groups[group_name].append(tool)

        selected = []
        for group_name in ordered_groups:
            if len(selected) >= top_k:
                break
            if groups[group_name]:
                selected.append(groups[group_name].pop(0))

        for tool in self.tool_summary:
            if len(selected) >= top_k:
                break
            if tool not in selected:
                selected.append(tool)

        return [self._format_result(tool, 0.0) for tool in selected[:top_k]]

    def retrieve(self, query, top_k=10):
        top_k = max(int(top_k or 0), 0)
        if top_k == 0 or not self.tool_summary:
            return []
        if self.index is not None:
            try:
                return self._retrieve_vector(query, top_k)
            except Exception as exc:
                self.index = None
                self.retrieval_method = "keyword_overlap_v1"
                self.disabled_reason = f"{type(exc).__name__}: {exc}"
        keyword_results = self._retrieve_keyword(query, top_k)
        if keyword_results:
            return keyword_results
        return self._default_candidates(top_k)
