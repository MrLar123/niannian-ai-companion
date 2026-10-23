# -*- coding: utf-8 -*-
"""
念念 · 记忆编排层（LLM 侧）

把 memory_store（纯存储）与大模型接起来，实现两段式流水线：

  第一段 抽取：从「用户这一轮说了什么 + 她答了什么」里抽出值得长期记住的事实
                → 借鉴 mem0 的 FACT_RETRIEVAL_PROMPT（Apache-2.0）
  第二段 消解：把新事实与已有记忆比对，决定 ADD / UPDATE / DELETE / NONE
                → 借鉴 mem0 的 DEFAULT_UPDATE_MEMORY_PROMPT（Apache-2.0）

与 mem0 的差异（为什么不能直接照搬）：
1. mem0 用向量库检索"可能相关"的旧记忆做比对；本机无 embedding 服务，
   故改用 FTS5 全文检索（memory_store.search）取候选，语义召回弱一些，
   但配合「同文本去重」+「短列表全量比对」两条兜底，实际够用。
2. mem0 一次调用只处理一条消息；我们的对话是短句闲聊（1-3 句），
   故按**轮次**抽取，且对无人称价值的内容直接跳过，省一次 LLM 调用。
3. 全部调用走 or_proxy 的 auto 路由（国内直连、低延迟）。
   抽取失败绝不阻塞主对话——宁可漏记，不可让她答不上话。
"""
import json
import re
import threading
import time

# ------------------------------------------------------------------ 提示词

# 第一段：事实抽取。核心约束沿用 mem0：只从用户消息取，不取助手消息。
# 针对中文女友场景做了裁剪：去掉 mem0 里的商业/服务类目，换成生活化类目。
EXTRACT_PROMPT = """你是「念念」的记忆整理器。请从下面这轮对话中，提取值得**长期记住**的、关于「我」（用户）的信息。

# 只提取用户说的话。她的回复只用于理解上下文，不要把她的话当成我的信息。
# 宁缺勿滥：闲聊、寒暄、情绪化的感叹、没有信息量的内容，一律不提取。

值得记住的类别：
1. 称呼与身份：名字、昵称、职业、所在城市、家庭成员、宠物
2. 喜好与厌恶：食物、饮品、音乐、游戏、影视、品牌、颜色
3. 作息与习惯：起床/睡觉时间、加班规律、周末安排、饮食习惯、健身
4. 计划与事件：即将到来的安排、目标、约定、纪念日、生日
5. 身体状况：过敏、忌口、慢性病、近期不适
6. 情绪与偏好：对特定话题的态度、雷区、在意的事

每条事实写成**简洁的第三人称陈述句**，用中文，不加编号、不加句号重复。
例如「用户喜欢喝美式咖啡」「用户养了一只叫豆豆的猫」「用户经常加班到深夜」。

严格返回 JSON，不要任何解释文字：
{"facts": ["事实1", "事实2"]}

如果没有任何值得记住的信息，返回 {"facts": []}

当前时间：%s

这轮对话：
用户：%s
她：%s
"""

# 第二段：冲突消解。四操作语义直接对应 mem0（ADD/UPDATE/DELETE/NONE）。
# 关键增强：允许同时给出画像槽位更新（mem0 没有这个，但它对"记住你的名字"很关键）。
RESOLVE_PROMPT = """你是记忆管理器，负责维护关于「我」（用户）的长期记忆库。
你可以执行四种操作：ADD（新增）、UPDATE（改写）、DELETE（删除）、NONE（不动）。

判断规则：
- ADD：新信息不在已有记忆里 → 新建一条
- UPDATE：已有记忆与新信息讲的是**同一件事**，但新信息更准确/更完整 → 改写它，保持 id 不变
  · 例：已有「用户喜欢喝咖啡」，新信息「用户喜欢喝美式咖啡」→ UPDATE
  · 例：已有「用户喜欢喝咖啡」，新信息「用户喜欢喝咖啡」→ NONE（意思相同，不必动）
- DELETE：新信息与已有记忆**直接矛盾** → 删除旧的
  · 例：已有「用户喜欢喝咖啡」，新信息「用户戒咖啡了」→ DELETE 旧的
- NONE：已经包含或无关 → 不动

另外，如果这轮信息里出现了用户的**画像**（可填的槽位见下），一并输出：
  槽位可选项：name(称呼) job(职业) city(城市) likes(喜好) dislikes(讨厌) habits(作息) important(重要日期/关系)

严格返回 JSON，不要解释文字：
{
  "memory": [{"id": 原id, "text": "最终文本", "event": "ADD|UPDATE|DELETE|NONE"}],
  "profile": {"name": "小家伙"}
}
其中：新增的条目 id 填 0；profile 无可更新内容时给空对象 {}。

已有记忆（id : 文本）：
%s

这一轮新提取到的事实：
%s
"""

# 第三段：滚动摘要。历史超出窗口后，把被挤出去的部分压成一段摘要，
# 保证她没有"彻底忘记前面聊过什么"。
SUMMARY_PROMPT = """请把下面这段对话压缩成一段简洁的摘要，用于让「念念」记住更早聊过的内容。

要求：
- 用中文，150 字以内，只保留有长期价值的信息（聊了什么主题、我的态度、达成的结论、约定）
- 第三人称陈述，不要出现"用户说""她回答"这类冗余框架
- 如果涉及我（用户）的个人信息，务必保留
- 直接输出摘要正文，不要任何前缀、标题或解释

%s已有摘要（若有，请合并进去）：
%s

需要压缩的新对话：
%s
"""


# ------------------------------------------------------------------ 解析

def _parse_json(text):
    """从模型输出里抠出 JSON。模型常带 ```json 围栏或前后废话，这里统一兜掉。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        pass
    # 退一步：抓第一个完整的 {...}
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(s[start:end + 1])
        except Exception:
            return None
    return None


# ------------------------------------------------------------------ 编排

class MemoryBrain(object):
    """把 store 与 LLM 串成完整流水线。

    call_llm(prompt) -> str  由外部注入（后端传 brain_chat 风格的调用），
    这样本类不依赖 requests，可离线单测（见文件末尾）。
    """

    def __init__(self, store, call_llm, log=None, enabled=True):
        self.store = store
        self.call_llm = call_llm
        self.log = log or (lambda *a: None)
        self.enabled = enabled
        self._busy = threading.Lock()      # 同一时刻只跑一次抽取，避免并发写库
        self.stats = {"extracted": 0, "added": 0, "updated": 0,
                      "deleted": 0, "skipped": 0, "errors": 0}

    # -------------------------------------------- 主入口

    def observe_async(self, user_msg, reply):
        """异步消化一轮对话。绝不阻塞主对话链路——她必须先答上话。"""
        if not self.enabled:
            return
        t = threading.Thread(target=self._observe, args=(user_msg, reply), daemon=True)
        t.start()

    def _observe(self, user_msg, reply):
        if not self._busy.acquire(blocking=False):
            self.stats["skipped"] += 1
            return
        try:
            self._observe_locked(user_msg, reply)
        except Exception as e:
            self.stats["errors"] += 1
            self.log("记忆消化失败: %r" % (e,))
        finally:
            self._busy.release()

    def _observe_locked(self, user_msg, reply):
        # 第一段：抽取
        facts = self.extract(user_msg, reply)
        if not facts:
            return
        self.stats["extracted"] += len(facts)
        # 第二段：消解
        self.resolve(facts)

    # -------------------------------------------- 第一段

    def extract(self, user_msg, reply):
        now = time.strftime("%Y-%m-%d %H:%M")
        prompt = EXTRACT_PROMPT % (now, user_msg, reply)
        try:
            raw = self.call_llm(prompt)
        except Exception as e:
            # 故障隔离：抽取是旁路，绝不能因为它把主对话带崩
            self.stats["errors"] += 1
            self.log("抽取调用失败: %r" % (e,))
            return []
        data = _parse_json(raw)
        if not isinstance(data, dict):
            self.log("抽取返回非 JSON: %s" % str(raw)[:120])
            return []
        out = []
        for f in (data.get("facts") or []):
            if isinstance(f, str) and 2 <= len(f.strip()) <= 120:
                out.append(f.strip())
        return out[:6]                      # 单轮最多 6 条，防模型话痨

    # -------------------------------------------- 第二段

    def resolve(self, facts):
        # 取候选旧记忆：用所有事实拼成一个查询，召回一批相关条目
        cand = self.store.search(" ".join(facts), limit=12)
        cand_map = {c["id"]: c["text"] for c in cand}
        # 候选为空时也要走一遍——否则画像更新永远不会发生
        old_lines = "\n".join("%d : %s" % (i, t) for i, t in cand_map.items()) or "（暂无）"
        new_lines = "\n".join("- " + f for f in facts)
        prompt = RESOLVE_PROMPT % (old_lines, new_lines)
        try:
            raw = self.call_llm(prompt)
        except Exception as e:
            self.stats["errors"] += 1
            self.log("消解调用失败: %r" % (e,))
            return
        data = _parse_json(raw)
        if not isinstance(data, dict):
            self.log("消解返回非 JSON: %s" % str(raw)[:120])
            return

        # 画像先落（它不依赖 id 合法性）
        prof = data.get("profile")
        if isinstance(prof, dict):
            for k, v in prof.items():
                if isinstance(v, str) and self.store.set_profile(k, v):
                    self.log("画像更新 %s=%s" % (k, v))

        # 再处理记忆条目
        mem = data.get("memory")
        if not isinstance(mem, list):
            return
        seen = set()
        for item in mem:
            if not isinstance(item, dict):
                continue
            ev = str(item.get("event") or "NONE").upper()
            text = (item.get("text") or "").strip()
            try:
                fid = int(item.get("id") or 0)
            except (TypeError, ValueError):
                fid = 0
            if ev == "ADD":
                if text and text not in seen and self.store.add_fact(text):
                    seen.add(text)
                    self.stats["added"] += 1
                    self.log("记住: %s" % text)
            elif ev == "UPDATE":
                if fid in cand_map and text and self.store.update_fact(fid, text):
                    self.stats["updated"] += 1
                    self.log("改写: %s -> %s" % (cand_map[fid], text))
            elif ev == "DELETE":
                if fid in cand_map and self.store.delete_fact(fid):
                    self.stats["deleted"] += 1
                    self.log("忘记: %s" % cand_map[fid])

    # -------------------------------------------- 摘要滚动

    def summarize(self, dropped):
        """把滑出窗口的历史压成摘要。dropped 是 [{role, content}, ...]。"""
        if not dropped:
            return False
        lines = []
        for m in dropped:
            who = "我" if m.get("role") == "user" else "她"
            lines.append("%s：%s" % (who, m.get("content") or ""))
        old = self.store.get_meta("rolling_summary") or "（无）"
        prompt = SUMMARY_PROMPT % ("", old, "\n".join(lines))
        try:
            s = (self.call_llm(prompt) or "").strip()
        except Exception as e:
            self.log("摘要生成失败: %r" % (e,))
            return False
        if not s or len(s) < 6:
            return False
        # 硬截断：摘要不断膨胀会把上下文吃光
        if len(s) > 400:
            s = s[:400] + "…"
        self.store.set_meta("rolling_summary", s)
        self.log("摘要已滚动更新（%d 条历史 -> %d 字）" % (len(dropped), len(s)))
        return True

    def get_context(self, query):
        """给主对话用的记忆上下文块。失败返回空串，绝不影响主流程。"""
        try:
            return self.store.build_context(query)
        except Exception as e:
            self.log("记忆上下文拼装失败: %r" % (e,))
            return ""


# ------------------------------------------------------------------ 自测
if __name__ == "__main__":
    import os
    import tempfile
    from memory_store import MemoryStore

    tmp = os.path.join(tempfile.gettempdir(), "_mem_brain_test.db")
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + suf)
        except OSError:
            pass

    # 用假 LLM 驱动，验证编排逻辑（不需要联网）
    # ★ 判定只看"这轮对话"那一段（模板里自带的示例词会误触发关键词匹配，
    #   所以这里额外切出 prompt 末尾的用户消息区，再匹配唯一哨兵词）。
    class FakeLLM(object):
        def __init__(self):
            self.calls = []

        @staticmethod
        def _turn(prompt):
            """取「用户：xxx」之后的正文，避开模板自带示例。"""
            i = prompt.rfind("用户：")
            return prompt[i:] if i >= 0 else prompt

        def __call__(self, prompt):
            self.calls.append(prompt)
            if "记忆整理器" in prompt:
                turn = self._turn(prompt)
                if "ZZQCOFFEE" in turn:
                    return '{"facts": ["用户喜欢喝美式咖啡", "用户经常加班到深夜"]}'
                if "ZZQCAT" in turn:
                    return '{"facts": ["用户养了一只叫豆豆的猫"]}'
                return '{"facts": []}'
            if "记忆管理器" in prompt:
                if "豆豆" in prompt:
                    return json.dumps({"memory": [
                        {"id": 0, "text": "用户养了一只叫豆豆的猫", "event": "ADD"}],
                        "profile": {"name": "小家伙"}}, ensure_ascii=False)
                if "美式" in prompt:
                    return json.dumps({"memory": [
                        {"id": 0, "text": "用户喜欢喝美式咖啡", "event": "ADD"},
                        {"id": 0, "text": "用户经常加班到深夜", "event": "ADD"}],
                        "profile": {}}, ensure_ascii=False)
                return json.dumps({"memory": [], "profile": {}}, ensure_ascii=False)
            if "压缩成一段简洁的摘要" in prompt:
                return "我之前说过自己喜欢喝美式咖啡，也提过经常加班。"
            return "{}"

    ok = True

    def check(name, cond):
        global ok
        print(("  PASS " if cond else "  FAIL ") + name)
        ok = ok and cond

    fake = FakeLLM()
    store = MemoryStore(tmp)
    logs = []
    brain = MemoryBrain(store, fake, log=lambda m: logs.append(m))

    print("[1] 事实抽取")
    f = brain.extract("我最近天天加班，靠 ZZQCOFFEE 续命", "又熬夜？活该。")
    check("抽出 2 条", len(f) == 2)
    check("含咖啡事实", any("美式" in x for x in f))

    print("[2] 空内容不产噪声")
    check("闲聊抽出 0 条", brain.extract("在吗", "嗯。") == [])

    print("[3] 消解流水线（同步调用版）")
    brain._observe_locked("我最近天天加班，靠 ZZQCOFFEE 续命", "又熬夜？活该。")
    check("入库 2 条", store.count() == 2)
    check("added 计数", brain.stats["added"] == 2)

    print("[4] 画像随消解落库")
    brain._observe_locked("我养了只 ZZQCAT 叫豆豆", "哦？")
    check("画像 name 落库", store.get_profile().get("name") == "小家伙")
    check("猫已记住", any("豆豆" in x["text"] for x in store.all_facts()))

    print("[5] 摘要滚动")
    dropped = [{"role": "user", "content": "我喜欢美式"},
               {"role": "assistant", "content": "知道了"}]
    check("摘要写入成功", brain.summarize(dropped))
    check("摘要可读回", "美式" in (store.get_meta("rolling_summary") or ""))

    print("[6] 上下文拼装")
    ctx = brain.get_context("我今天又在加班")
    check("含画像", "小家伙" in ctx)
    check("含记忆", "【相关记忆】" in ctx)
    check("含摘要", "【更早的聊天摘要】" in ctx)

    print("[7] 故障隔离")
    def boom(p):
        raise RuntimeError("模拟大脑挂掉")
    b2 = MemoryBrain(store, boom, log=lambda m: None)
    check("抽取失败返回空列表", b2.extract("测试", "测试") == [])
    n_before = store.count()
    b2._observe_locked("测试", "测试")
    check("失败不崩且计数>0", b2.stats["errors"] >= 1)
    check("失败不改动库", store.count() == n_before)

    print("[8] 脏输出容错")
    b3 = MemoryBrain(store, lambda p: "```json\n{\"facts\": [\"用户爱喝拿铁\"]}\n```",
                     log=lambda m: None)
    check("剥离代码围栏", b3.extract("我爱喝拿铁", "好") == ["用户爱喝拿铁"])
    b4 = MemoryBrain(store, lambda p: "模型抽风了没给 JSON", log=lambda m: None)
    check("非 JSON 安全返回", b4.extract("随便", "随便") == [])
    b5 = MemoryBrain(store, lambda p: '{"facts": [1,2,3]}', log=lambda m: None)
    check("非字符串条目被过滤", b5.extract("随便", "随便") == [])

    print("[9] 长文本裁剪")
    b6 = MemoryBrain(store, lambda p: json.dumps(
        {"facts": ["用户" + "很" * 200]}), log=lambda m: None)
    check("超长事实被丢弃", b6.extract("x", "y") == [])

    store.close()
    print("\n结果:", "全部通过" if ok else "存在失败")
    raise SystemExit(0 if ok else 1)
