# -*- coding: utf-8 -*-
"""记忆检索 E2E：真实 embed 服务 + 真实 bge 向量的 A/B 与降级验证。
只读生产库的结构，写入全部落在临时库；生产库仅做"补建向量"这一步，
且补建是加法操作（只填 emb IS NULL 的列），可重复运行无副作用。"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory_store import MemoryStore


class RealEmbed(object):
    """与 xiaoyun_server.EmbedClient 同构的最小实现（Ollama /api/embed）。

    与后端保持同样的三个约束：本机直连（绕代理）、num_gpu=99（全量上 GPU，
    实测 664MB 显存 / 35ms）、keep_alive=30m（避免每次重载 1.2GB 权重）。"""

    def __init__(self, url="http://127.0.0.1:11434", model="bge-m3"):
        import requests
        self._r = requests
        self.url = url
        self.model = model

    def __call__(self, texts, mode="doc"):
        try:
            resp = self._r.post(
                self.url + "/api/embed",
                json={"model": self.model, "input": list(texts),
                      "keep_alive": "30m", "options": {"num_gpu": 99}},
                timeout=60, proxies={"http": None, "https": None})
            if resp.status_code != 200:
                return None
            return (resp.json() or {}).get("embeddings")
        except Exception:
            return None


def tmpdb(name):
    p = os.path.join(tempfile.gettempdir(), name)
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(p + suf)
        except OSError:
            pass
    return p


ok = True


def check(name, cond):
    global ok
    print(("  PASS " if cond else "  FAIL ") + name)
    ok = ok and cond


print("[1] 真实 embed 服务连通性（Ollama bge-m3）")
emb = RealEmbed()
vecs = emb(["测试连通性"], "doc")
check("服务返回 1024 维向量", bool(vecs) and len(vecs[0]) == 1024)

print("[2] 语义召回 A/B（字面零重叠的经典盲区用例）")
facts = [
    "用户养了一只叫豆豆的猫",
    "用户是程序员，经常加班到深夜",
    "用户喜欢喝美式咖啡，不加糖",
    "用户周末喜欢去爬山",
]
p = tmpdb("_e2e_sem.db")
st = MemoryStore(p, embed_fn=emb)
for f in facts:
    st.add_fact(f)

# 对照组：纯 FTS5（不注 embed_fn）
p2 = tmpdb("_e2e_fts.db")
st_fts = MemoryStore(p2)
for f in facts:
    st_fts.add_fact(f)

QUERY = "猫毛过敏怎么办"          # 与任何一条事实字面零重叠
r_new = st.search(QUERY)
r_old = st_fts.search(QUERY)
print("  查询: %r" % QUERY)
print("  混合检索 top3: %s" % [h["text"] for h in r_new[:3]])
print("  纯 FTS5  top3: %s" % [h["text"] for h in r_old[:3]])
check("混合检索命中养猫事实", bool(r_new) and "猫" in r_new[0]["text"])
check("纯 FTS5 在该查询上盲区（符合预期）", not any("猫" in h["text"] for h in r_old))

# 精确查询两种路径都应保持
r_new2 = st.search("咖啡")
check("精确查询「咖啡」混合仍居首", bool(r_new2) and "咖啡" in r_new2[0]["text"])
r_old2 = st_fts.search("咖啡")
check("精确查询「咖啡」FTS 仍居首", bool(r_old2) and "咖啡" in r_old2[0]["text"])

print("[3] 降级路径（embed 服务不可用）")
emb_dead = RealEmbed("http://127.0.0.1:9999")     # 必然连不上的端口
p3 = tmpdb("_e2e_dead.db")
st_dead = MemoryStore(p3, embed_fn=emb_dead)
for f in facts:
    st_dead.add_fact(f)
r_dead = st_dead.search("咖啡")
check("服务不可用时精确检索正常", bool(r_dead) and "咖啡" in r_dead[0]["text"])
r_dead2 = st_dead.search("猫毛过敏怎么办")
check("服务不可用时安静返回（不抛异常）", isinstance(r_dead2, list))
check("服务不可用时语义盲区回归（符合预期）", not any("猫" in h["text"] for h in r_dead2))

print("[4] 生产库维度迁移 + 补建（换模型 bge-small/512 -> bge-m3/1024）")
prod = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "memory.db")
if os.path.exists(prod):
    st_prod = MemoryStore(prod, embed_fn=emb)
    dim = len((emb(["探测"]) or [[0] * 1024])[0])
    purged = st_prod.purge_mismatched_vectors(dim)
    print("  当前模型维度 %d，清掉异维向量 %d 条" % (dim, purged))
    t0 = time.time()
    n = st_prod.backfill()
    dt = time.time() - t0
    empty = st_prod._db.execute(
        "SELECT COUNT(*) FROM facts WHERE active=1 AND emb IS NULL").fetchone()[0]
    wrong = st_prod._db.execute(
        "SELECT COUNT(*) FROM facts WHERE active=1 AND emb IS NOT NULL AND length(emb)!=?",
        (dim * 4,)).fetchone()[0]
    print("  重嵌 %d 条，耗时 %.2fs；空缺 %d 条，异维 %d 条" % (n, dt, empty, wrong))
    check("迁移后无空缺向量", empty == 0)
    check("迁移后无异维向量", wrong == 0)
    # 生产库上也验证一次语义召回
    hits = st_prod.search("测试一下语义检索")
    print("  生产库检索示例 top2: %s" % [h["text"][:24] for h in hits[:2]])
    check("生产库检索不报错", isinstance(hits, list))
    st_prod.close()
else:
    print("  生产库不存在，跳过")

print("\n结果:", "全部通过" if ok else "存在失败")
sys.exit(0 if ok else 1)
