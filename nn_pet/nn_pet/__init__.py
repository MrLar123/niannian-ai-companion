# -*- coding: utf-8 -*-
"""念念桌宠重构版（class 封装）。

把原 xiaoyun/ 下分散的全局函数与模块级状态，按职责重组为类：

- AppConfig        配置加载（config.json + 人设 JSON + 环境变量覆盖）
- BrainClient      对话大脑客户端（or_proxy / OpenAI 兼容 chat completions）
- TTSEngine        语音合成（豆包 volc 云端 + edge-tts 保底 + 情绪调度）
- HistoryStore     会话历史（seq 广播 + 播放权 LAST_CLAIM）
- VoiceSession     语音会话时序（playing 上报 / resume 委派 / 自听防护）
- PetServer        Flask 后端总装（路由注册 + 后台线程）

原版为单文件过程式脚本（xiaoyun_server.py，4174 行）；本包是等价的 class
骨架实现，聚焦核心对话链路（/api/chat /api/health /api/last /api/history），
完整功能（语音路由、感知、记忆、托盘等）按原文件模块边界逐类迁移。

设计约定：
- 每个类只依赖显式传入的配置，不读全局；
- 跨线程共享状态集中在带锁的容器类（HistoryStore / VoiceSession）；
- 路由层薄：Flask handler 只做参数搬运，业务在类方法里。
"""
