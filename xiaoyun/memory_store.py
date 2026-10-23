# -*- coding: utf-8 -*-
"""
念念 · 长期记忆存储层（零 LLM 依赖，纯确定性存储与检索）

设计来源（借鉴，非照搬）：
- 记忆抽取与冲突消解的两段式流水线，来自 mem0（Apache-2.0）的
  FACT_RETRIEVAL_PROMPT / DEFAULT_UPDATE_MEMORY_PROMPT（ADD/UPDATE/DELETE/NONE 四操作）。
  mem0 的存储层依赖 qdrant + openai + posthog，本机无 embedding 服务，
  故**不引入其运行时代码**，仅借鉴其提示词契约与数据模型，存储改用标准库 sqlite3。
- 「画像 / 事实 / 摘要」三层划分，参考 Letta(MemGPT) 的 core memory + archival memory 分层思路。

为什么有向量检索：
  2026-10-19 起本地有了 embedding 服务（embed_service.py，8092，
  bge-small-zh-v1.5 跑在 hunyuan3d/venv 的 torch cu128 + RTX 5050 上）。
  FTS5 bigram 只有"字面命中"能力，对「我养了只猫」×「猫毛过敏怎么办」
  这类同义改写完全盲区 —— 语义召回正是向量补位的地方。
  检索改为 FTS5（精确）+ 向量（语义）RRF 融合：两组分数不同尺度
  （BM25 负分 vs 余弦），RRF 只用名次加权，天然可融合。
  服务没起时 embed_fn=None，自动退化为纯 FTS5，行为与旧版一致。

数据落在 data/memory.db，重启不丢。
"""
import array
import os
import re
import sqlite3
import threading
import time


# ---------------------------------------------------------------- 中文切分

_CJK = r"\u4e00-\u9fff\u3400-\u4dbf"
_KEEP = re.compile(r"[^\w" + _CJK + r"]+", re.UNICODE)


def bigrams(text):
    """中文按 2 字滑窗切分，英文数字按整词保留。

    为什么是 bigram 而不是分词：本机无 jieba 等分词库，引入即多一个依赖；
    bigram 对检索场景足够（「美式咖啡」→「美式 式咖 咖啡」，任一片段都能命中），
    且对未知词（人名、新词）天然鲁棒——分词库的词典覆盖不到时反而失效。
    """
    if not text:
        return ""
    s = _KEEP.sub(" ", str(text).lower())
    out = []
    for chunk in s.split():
        if not chunk:
            continue
        if re.search(r"[" + _CJK + r"]", chunk):
            # 中文块：2 字滑窗；单字块原样保留，否则 1 字查询无法命中
            if len(chunk) == 1:
                out.append(chunk)
            else:
                out.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
        else:
            out.append(chunk)          # 英文/数字整词，避免 "coffee" 被切成 "co ff ee"
    return " ".join(out)


# ---------------------------------------------------------------- 存储

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  text       TEXT    NOT NULL,
  text_bg    TEXT    NOT NULL,
  kind       TEXT    DEFAULT 'fact',
  created_ts REAL    NOT NULL,
  updated_ts REAL    NOT NULL,
  hits       INTEGER DEFAULT 0,
  last_hit   REAL    DEFAULT 0,
  active     INTEGER DEFAULT 1,
  emb        BLOB
);
CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(active);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
  text_bg,
  content='facts',
  content_rowid='id',
  tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, text_bg) VALUES (new.id, new.text_bg);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, text_bg) VALUES ('delete', old.id, old.text_bg);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, text_bg) VALUES ('delete', old.id, old.text_bg);
  INSERT INTO facts_fts(rowid, text_bg) VALUES (new.id, new.text_bg);
END;

CREATE TABLE IF NOT EXISTS profile(
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS meta(
  key   TEXT PRIMARY KEY,
  value TEXT
);
"""

# 画像允许的键（收敛取值，防止 LLM 自由发挥写出五花八门的键名）
PROFILE_KEYS = {
    "name":      "称呼",
    "job":       "职业",
    "city":      "所在城市",
    "likes":     "喜好",
    "dislikes":  "讨厌",
    "habits":    "作息习惯",
    "important": "重要日期/关系",
}


class MemoryStore(object):
    """线程安全的记忆存储。所有方法都不调用 LLM——LLM 编排放在服务层，
    好处是这一层可以被单元测试完全覆盖（本文件自带自测，见文件末尾）。

    embed_fn(texts, mode) -> list[list[float]] | None
        可选的语义向量回调（mode: "doc" 入库 / "query" 检索）。由后端注入
        EmbedClient；服务不可用时传 None，检索自动退化为纯 FTS5。
    """

    def __init__(self, path, embed_fn=None):
        self.path = path
        self._embed = embed_fn
        self._lock = threading.RLock()
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            self._migrate()
            self._db.commit()

    def _migrate(self):
        """存量库补 emb 列（SCHEMA 的 CREATE IF NOT EXISTS 不会给旧表加列）。
        调用前需已持有 self._lock。"""
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(facts)")}
        if "emb" not in cols:
            self._db.execute("ALTER TABLE facts ADD COLUMN emb BLOB")

    # ------------------------------------------------ facts 写

    def add_fact(self, text, kind="fact"):
        """新增一条事实；与存量**完全相同**的文本会被忽略（返回 None）。"""
        text = (text or "").strip()
        if not text:
            return None
        bg = bigrams(text)
        now = time.time()
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM facts WHERE active=1 AND text=?", (text,)).fetchone()
            if row:
                return None
            cur = self._db.execute(
                "INSERT INTO facts(text, text_bg, kind, created_ts, updated_ts)"
                " VALUES(?,?,?,?,?)", (text, bg, kind, now, now))
            fid = cur.lastrowid
            self._db.commit()
        self._embed_fact(fid, text)      # 锁外算向量；失败留 NULL，等 backfill 补
        return fid

    def update_fact(self, fid, text):
        """改写一条事实（mem0 的 UPDATE 语义：保留同一 id，覆盖文本）。"""
        text = (text or "").strip()
        if not text:
            return False
        with self._lock:
            r = self._db.execute(
                "UPDATE facts SET text=?, text_bg=?, updated_ts=?, emb=NULL"
                " WHERE id=? AND active=1",
                (text, bigrams(text), time.time(), fid))
            self._db.commit()
            if r.rowcount <= 0:
                return False
        self._embed_fact(fid, text)      # 文本变了，旧向量作废，重算
        return True

    def _embed_fact(self, fid, text):
        """给单条事实算向量入库。语义索引是增强项：任何失败都静默跳过
        （留 NULL），由 backfill() 事后补建，绝不影响主流程。"""
        if not self._embed or not text:
            return
        try:
            vecs = self._embed([text], "doc")
            if not vecs or not vecs[0]:
                return
            blob = array.array("f", vecs[0]).tobytes()
            with self._lock:
                self._db.execute("UPDATE facts SET emb=? WHERE id=?", (blob, fid))
                self._db.commit()
        except Exception:
            pass

    def purge_mismatched_vectors(self, dim):
        """把维度与当前 embedding 模型不一致的向量置空，返回清理条数。

        换模型（bge-small 512 维 -> bge-m3 1024 维）之后必须走这一步：
        `_vector_hits` 会跳过长度不符的向量，于是旧事实"既不报错、也永远
        不再被语义召回"—— 静默失忆比报错更难发现。清空后由 backfill() 重嵌。
        """
        if not dim or dim <= 0:
            return 0
        with self._lock:
            r = self._db.execute(
                "UPDATE facts SET emb=NULL WHERE emb IS NOT NULL AND length(emb) != ?",
                (int(dim) * 4,))      # float32 = 4 字节
            self._db.commit()
            return max(0, r.rowcount or 0)

    def backfill(self, batch=32):
        """给 emb 为空的存量事实补建向量。返回补建条数。"""
        if not self._embed:
            return 0
        n = 0
        while True:
            with self._lock:
                rows = self._db.execute(
                    "SELECT id, text FROM facts WHERE active=1 AND emb IS NULL"
                    " LIMIT ?", (batch,)).fetchall()
            if not rows:
                break
            try:
                vecs = self._embed([r["text"] for r in rows], "doc")
            except Exception:
                break
            if not vecs or len(vecs) != len(rows):
                break
            with self._lock:
                for r, v in zip(rows, vecs):
                    if v:
                        self._db.execute("UPDATE facts SET emb=? WHERE id=?",
                                         (array.array("f", v).tobytes(), r["id"]))
                self._db.commit()
            n += len(rows)
            if len(rows) < batch:
                break
        return n

    def delete_fact(self, fid):
        """软删（mem0 的 DELETE 语义：矛盾信息才删）。保留行便于回溯。"""
        with self._lock:
            r = self._db.execute(
                "UPDATE facts SET active=0, updated_ts=? WHERE id=? AND active=1",
                (time.time(), fid))
            self._db.commit()
            return r.rowcount > 0

    def clear_facts(self):
        with self._lock:
            self._db.execute("UPDATE facts SET active=0, updated_ts=?", (time.time(),))
            self._db.commit()

    # ------------------------------------------------ facts 读

    def search(self, query, limit=8, min_len=2):
        """混合检索：FTS5 bigram（精确命中）+ 本地向量（语义命中），RRF 融合。

        RRF（Reciprocal Rank Fusion，k=60）只按名次计分 —— BM25 是负分、
        余弦是 [-1,1]，两组分数不同尺度没法直接加权，RRF 天然回避了这一点。
        向量路径补的正是 FTS 的结构性盲区：同义改写（「养了只猫」×「猫毛
        过敏怎么办」）字面零重叠，bigram 一个都命中不了。
        embed_fn 不可用 / 库里还没有向量时，退化为纯 FTS5（旧行为）。
        """
        raw = (query or "").strip()
        if not raw:
            return []

        # 1) 语义召回（HTTP 调用，必须在锁外）
        vec_hits = self._vector_hits(raw, limit)

        # 2) FTS5 精确召回（+ LIKE 兜底），锁内
        q = bigrams(raw)
        terms = q.split()
        match = " OR ".join('"%s"' % t for t in terms)
        with self._lock:
            rows = []
            if terms:
                try:
                    rows = self._db.execute(
                        "SELECT f.id, f.text, f.kind, f.hits, f.updated_ts, bm25(facts_fts) AS score"
                        " FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid"
                        " WHERE facts_fts MATCH ? AND f.active=1"
                        " ORDER BY score LIMIT ?", (match, max(limit * 3, 12))).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            # 回退：索引里存的是 bigram，单字中文词（「猫」「书」）永远命中不了，
            # 中文里这类查询很常见，用 LIKE 兜一层。只在 FTS 空手而归时才走，
            # 不参与 BM25 排序，避免拖慢正常路径。
            if not rows:
                like_terms = [t for t in _KEEP.sub(" ", raw.lower()).split() if t][:4]
                if like_terms:
                    where = " AND ".join(["text LIKE ?"] * len(like_terms))
                    rows = self._db.execute(
                        "SELECT id, text, kind, hits, updated_ts, 0.0 AS score"
                        " FROM facts WHERE active=1 AND " + where +
                        " ORDER BY updated_ts DESC LIMIT ?",
                        tuple("%" + t + "%" for t in like_terms) + (max(limit * 3, 12),)
                    ).fetchall()

        # 3) 无语义召回：保持旧行为（BM25 + 热度加权，越小越相关）
        if not vec_hits:
            out = []
            for r in rows:
                boost = 1.0 + 0.35 * min(3.0, (r["hits"] or 0) ** 0.5)
                out.append({"id": r["id"], "text": r["text"], "kind": r["kind"],
                            "score": r["score"] / boost})
            out.sort(key=lambda x: x["score"])
            return out[:limit]

        # 4) RRF 融合（k=60 是原论文推荐值；名次从 1 起算）
        k = 60.0
        rrf, meta = {}, {}
        for rank, r in enumerate(rows):
            fid = r["id"]
            boost = 1.0 + 0.35 * min(3.0, (r["hits"] or 0) ** 0.5)
            rrf[fid] = rrf.get(fid, 0.0) + boost / (k + rank + 1)
            meta[fid] = r
        for rank, (fid, sim) in enumerate(vec_hits):
            rrf[fid] = rrf.get(fid, 0.0) + 1.0 / (k + rank + 1)
            if fid not in meta:                    # 向量独有命中：补查文本
                with self._lock:
                    row = self._db.execute(
                        "SELECT id, text, kind, hits, updated_ts FROM facts WHERE id=?",
                        (fid,)).fetchone()
                if row:
                    meta[fid] = row
        out = []
        for fid, sc in rrf.items():
            r = meta.get(fid)
            if not r:
                continue
            out.append({"id": fid, "text": r["text"], "kind": r["kind"],
                        "score": -sc})             # 负分：沿用"越小越相关"惯例
        out.sort(key=lambda x: x["score"])
        return out[:limit]

    def _vector_hits(self, query, limit):
        """语义检索：查询向量 vs 库存向量的余弦（库存向量入库时已 L2 归一化，
        点积即余弦）。返回 [(id, sim)] 按 sim 降序。任何失败返回 []。"""
        if not self._embed or not query:
            return []
        try:
            vecs = self._embed([query], "query")
        except Exception:
            return []
        if not vecs or not vecs[0]:
            return []
        qv = array.array("f", vecs[0])
        qn = len(qv)
        with self._lock:
            rows = self._db.execute(
                "SELECT id, emb FROM facts WHERE active=1 AND emb IS NOT NULL"
            ).fetchall()
        out = []
        for r in rows:
            v = array.array("f")
            try:
                v.frombytes(bytes(r["emb"]))
            except Exception:
                continue
            if len(v) != qn:
                continue
            s = 0.0
            for i in range(qn):
                s += qv[i] * v[i]
            out.append((r["id"], s))
        out.sort(key=lambda x: -x[1])
        return out[:max(limit * 2, 8)]

    def all_facts(self, limit=200):
        with self._lock:
            rows = self._db.execute(
                "SELECT id, text, kind, hits, created_ts FROM facts"
                " WHERE active=1 ORDER BY updated_ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def touch(self, ids):
        """召回命中计数：用于上面的热度加权。"""
        if not ids:
            return
        now = time.time()
        with self._lock:
            self._db.executemany(
                "UPDATE facts SET hits=hits+1, last_hit=? WHERE id=?",
                [(now, i) for i in ids])
            self._db.commit()

    def count(self):
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM facts WHERE active=1").fetchone()[0]

    # ------------------------------------------------ 画像

    def set_profile(self, key, value):
        """写画像槽位。key 白名单外的一律丢弃（LLM 容易发明键名）。"""
        if key not in PROFILE_KEYS:
            return False
        value = (value or "").strip()
        if not value:
            return False
        with self._lock:
            self._db.execute(
                "INSERT INTO profile(key, value, updated_ts) VALUES(?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_ts=excluded.updated_ts", (key, value, time.time()))
            self._db.commit()
        return True

    def get_profile(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT key, value FROM profile ORDER BY updated_ts").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ------------------------------------------------ 元信息（摘要游标等）

    def get_meta(self, key, default=None):
        with self._lock:
            r = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key, value):
        with self._lock:
            self._db.execute(
                "INSERT INTO meta(key, value) VALUES(?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, "" if value is None else str(value)))
            self._db.commit()

    # ------------------------------------------------ 上下文拼装

    def build_context(self, query, limit=8, max_chars=700):
        """把画像 + 相关事实拼成一段可直接塞进 system 提示词的文本。
        返回空字符串表示无可注入内容（此时提示词里不会出现空标题的尴尬块）。"""
        parts = []
        prof = self.get_profile()
        if prof:
            items = ["%s：%s" % (PROFILE_KEYS[k], v)
                     for k, v in prof.items() if k in PROFILE_KEYS]
            if items:
                parts.append("【你记得的事】" + "；".join(items))

        hits = self.search(query, limit=limit) if query else []
        if hits:
            self.touch([h["id"] for h in hits])
            lines = "；".join(h["text"] for h in hits)
            if len(lines) > max_chars:
                lines = lines[:max_chars] + "…"
            parts.append("【相关记忆】" + lines)

        summ = self.get_meta("rolling_summary")
        if summ:
            parts.append("【更早的聊天摘要】" + summ)
        return "\n".join(parts)

    def close(self):
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass


# ---------------------------------------------------------------- 自测
if __name__ == "__main__":
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "_mem_selftest.db")
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + suf)
        except OSError:
            pass
    st = MemoryStore(tmp)
    ok = True

    def check(name, cond):
        global ok
        print(("  PASS " if cond else "  FAIL ") + name)
        ok = ok and cond

    print("[1] 写入与检索")
    for t, k in [("用户喜欢喝美式咖啡", "preference"),
                 ("用户是程序员，经常加班到深夜", "profile"),
                 ("用户养了一只叫豆豆的猫", "fact")]:
        st.add_fact(t, k)
    check("总数=3", st.count() == 3)
    check("检索「咖啡」", any("咖啡" in h["text"] for h in st.search("咖啡")))
    check("检索「加班」", any("加班" in h["text"] for h in st.search("加班")))
    check("检索「美式」", any("美式" in h["text"] for h in st.search("美式")))
    check("检索英文不误伤", st.search("coffee") == [])

    print("[2] 去重 / 更新 / 删除")
    check("重复文本不入库", st.add_fact("用户喜欢喝美式咖啡") is None and st.count() == 3)
    fid = st.search("猫")[0]["id"]
    check("update 改写", st.update_fact(fid, "用户养了一只叫豆豆的橘猫"))
    check("改写后可见", any("橘猫" in h["text"] for h in st.search("猫")))
    check("delete 软删", st.delete_fact(fid) and st.count() == 2)
    check("删除后不再召回", not any("橘猫" in h["text"] for h in st.search("猫")))

    print("[3] 画像")
    check("白名单外键被拒", st.set_profile("hack", "x") is False)
    check("白名单内键可写", st.set_profile("name", "小家伙"))
    st.set_profile("job", "程序员")
    check("画像读回", st.get_profile().get("name") == "小家伙")

    print("[4] 上下文拼装")
    st.set_meta("rolling_summary", "之前聊过 3D 建模的事")
    ctx = st.build_context("我最近加班很多")
    check("含画像块", "【你记得的事】" in ctx)
    check("含记忆块", "【相关记忆】" in ctx)
    check("含摘要块", "【更早的聊天摘要】" in ctx)
    check("空库查询不报错", MemoryStore(tmp).build_context("") != "")

    print("[5] 热度加权")
    h1 = st.search("加班")[0]
    for _ in range(5):
        st.touch([h1["id"]])
    check("命中计数累加", st.search("加班")[0]["id"] == h1["id"])

    print("[6] 持久化（重开连接）")
    st.close()
    st2 = MemoryStore(tmp)
    check("重开后事实仍在", st2.count() == 2)
    check("重开后画像仍在", st2.get_profile().get("name") == "小家伙")
    check("重开后摘要仍在", st2.get_meta("rolling_summary") == "之前聊过 3D 建模的事")
    st2.close()

    print("[7] 向量混合检索（假 embed_fn）")

    def _fake_vec(t):
        # 按关键词映射到三个正交方向：模拟"语义相同 -> 向量相近"
        if "猫" in t or "宠物" in t:
            return [1.0, 0.0, 0.0]
        if "咖啡" in t:
            return [0.0, 1.0, 0.0]
        if "加班" in t:
            return [0.0, 0.0, 1.0]
        return [0.0, 0.0, 0.0]

    def fake_embed(texts, mode="doc"):
        return [_fake_vec(t) for t in texts]

    tmp2 = os.path.join(tempfile.gettempdir(), "_mem_vec_test.db")
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(tmp2 + suf)
        except OSError:
            pass
    sv = MemoryStore(tmp2, embed_fn=fake_embed)
    sv.add_fact("用户养了一只猫")
    sv.add_fact("用户喜欢喝咖啡")
    sv.add_fact("用户经常加班")
    emb_n = sv._db.execute(
        "SELECT COUNT(*) FROM facts WHERE active=1 AND emb IS NOT NULL").fetchone()[0]
    check("add_fact 自动入库向量", emb_n == 3)

    # 语义命中：查询与事实字面零重叠（FTS/LIKE 都 miss），纯向量召回
    hits = sv.search("想聊聊宠物")
    check("语义命中（字面零重叠）", hits and hits[0]["text"] == "用户养了一只猫")

    # 精确命中仍在，且向量同分不把排序搅乱
    hits2 = sv.search("咖啡")
    check("精确命中仍居首", hits2 and hits2[0]["text"] == "用户喜欢喝咖啡")

    # FTS + 向量双通道命中同一条：不能重复出现
    hits3 = sv.search("猫")
    ids3 = [h["id"] for h in hits3]
    check("混合结果不重复", len(ids3) == len(set(ids3)) and len(ids3) >= 1)

    # 混合结果整体不超限
    check("混合结果不超 limit", len(sv.search("猫 咖啡 加班 宠物", limit=3)) <= 3)

    # 存量补建：清空向量后 backfill 应补齐
    sv._db.execute("UPDATE facts SET emb=NULL")
    sv._db.commit()
    check("backfill 补齐", sv.backfill() == 3)
    check("补建后可语义召回", sv.search("想聊聊宠物")[0]["text"] == "用户养了一只猫")

    # 换模型（维度变化）时的迁移：同维不动、异维清空、清空后可重建
    check("维度一致时不误清", sv.purge_mismatched_vectors(3) == 0)
    check("维度变化时清掉旧向量", sv.purge_mismatched_vectors(8) == 3)
    check("清空后语义召回为空（等重嵌）", sv.search("想聊聊宠物") == [])
    check("重嵌后恢复语义召回", sv.backfill() == 3 and
          sv.search("想聊聊宠物")[0]["text"] == "用户养了一只猫")

    # update 后向量应跟着更新（旧文本向量作废）
    fid = sv.search("咖啡")[0]["id"]
    sv.update_fact(fid, "用户喜欢喝茶")
    check("update 后按新文本召回", sv.search("喝茶") and
          any("茶" in h["text"] for h in sv.search("喝茶")))
    check("update 后旧词不再语义召回", sv.search("咖啡") == [] or
          all("咖啡" not in h["text"] for h in sv.search("咖啡")))
    sv.close()

    # embed_fn=None：退化为纯 FTS5，旧行为保持
    tmp3 = os.path.join(tempfile.gettempdir(), "_mem_noemb_test.db")
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(tmp3 + suf)
        except OSError:
            pass
    sn = MemoryStore(tmp3)
    sn.add_fact("用户喜欢喝咖啡")
    sn.add_fact("用户经常加班")
    check("无 embed 时精确检索正常", any("咖啡" in h["text"] for h in sn.search("咖啡")))
    check("无 embed 时语义盲区依旧（符合预期）", sn.search("想聊聊宠物") == [])
    sn.close()

    print("\n结果:", "全部通过" if ok else "存在失败")
    raise SystemExit(0 if ok else 1)
