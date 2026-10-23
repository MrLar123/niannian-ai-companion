# nn_pet — 念念桌宠 class 封装重构版

把 `xiaoyun/` 过程式脚本（xiaoyun_server.py 4174 行等）按职责重组为类。

## 结构
```
nn_pet/
├── nn_pet/               Python 包
│   ├── __init__.py       包说明与设计约定
│   ├── config.py         AppConfig      配置三层合并（env > 人设 JSON > Luna config.json）
│   ├── brain.py          BrainClient    对话大脑（or_proxy OpenAI 兼容，流式/非流式/探活/防静默回退）
│   ├── tts.py            TTSEngine      豆包 volc 云端 + edge-tts 保底 + 情绪调度
│   ├── voice.py          HistoryStore   seq 广播历史 + 播放权认领（防双页同播）
│   │                     VoiceSession   语音时序状态机（playing 上报/busy 闸/resume 委派）
│   ├── pipeline.py       ReplyPipeline  发布管线（人设注入→大脑→舞台指令清洗→入库广播）
│   └── server.py         PetServer      Flask 总装（路由薄层，业务在类方法）
├── shell_main.py         桌面壳入口（NN_SHELL_MODE=legacy 可直跑原壳过渡）
├── tools/
│   └── test_nn_pet.py    单元测试（32 项，无头，test_client 驱动）
└── README.md
```

## 设计约定
1. 每个类只依赖显式传入的配置，不读全局；
2. 跨线程共享状态集中在带锁容器（HistoryStore / VoiceSession）；
3. 路由层薄：Flask handler 只做参数搬运，业务在类方法里；
4. 舞台指令清洗只在 ReplyPipeline 唯一出口做（原铁律保留）；
5. TTS 合成失败自动回退 edge（她不会哑）。

## 运行测试
```
D:/LunaAI/project-nn/runtime/Miniconda3/python.exe tools\test_nn_pet.py
```

## 起服务
```
D:/LunaAI/project-nn/runtime/Miniconda3/python.exe nn_pet\server.py
```
（默认 8090，XIAOYUN_PORT 可覆盖）

## 与原版的关系
- 原版 `xiaoyun/xiaoyun_server.py` 继续可用（启停脚本走它）；
- 本包是核心对话链路（chat/last/history/health/preset/voice 时序）的
  class 化等价实现 + 单测；感知/记忆/托盘等外围按模块边界逐类迁移，
  原文件中 Catcher/MicButton/HotkeyThread 等本就是 class，可整类搬入。
