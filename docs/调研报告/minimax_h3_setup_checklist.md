# MiniMax H3 本地部署清单（RTX 5050 8GB / 16GB RAM 专用）

> **方案变更说明**：原方案 2（Wan 2.2 TI2V-5B）已被 MiniMax H3 方案替代——夸克下载已有 4 个 H3 核心文件（~23.3GB），且 5050 是 Blackwell(sm_120)，原生支持 H3 官方推荐的 NVFP4 文本编码器。Wan 2.2 的 0 字节失败下载残留已清理。
>
> ⚠️ **硬前提**：本机物理内存仅 **16GB**。H3 走权重流送（内存当仓库、显存当工作台），社区实测案例均要求 **32GB 内存起步**。首跑大概率触发内存瓶颈，需关闭浏览器等大内存程序再试；若确认内存不足，降级路径见第八节。

---

## 一、模型文件总清单（6 个，约 41GB）

| # | 文件 | 大小 | 状态 | 目录 |
|---|------|------|------|------|
| 1 | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 19.5GB（实际20.97GB） | ✅ 已就位 | `diffusion_models/` |
| 2 | `minimax_h3_video_vae_int8_convrot.safetensors` | 2.6GB | ✅ 已就位 | `vae/` |
| 3 | `minimax_h3_audio_vae_fp32.safetensors` | 0.58GB | ✅ 已就位（已去 `(1)` 后缀） | `vae/` |
| 4 | `minimax_h3_flf2v_lora_v1.safetensors` | 148MB | ✅ 已就位（首尾帧连续分镜用，可选） | `loras/` |
| 5 | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15.7GB | ✅ 已就位（aria2 下载完成，15.7GB 完整） | `text_encoders/` |
| 6 | `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` | 1.96GB | ✅ 已就位（aria2 下载完成） | `loras/` |

**官方仓库路径核实结果**（hf-mirror API 实查，2026-10-24）：
- 官方**没有** `pdd_acc/` 目录——PDD-Acc 是第三方节点方案（`ComfyUI-MiniMax-H3-PDD-Acc`），其权重不在官方仓库
- 官方自带的加速方案是 `loras/` 下的 **turbo LoRA**（8 步蒸馏，FL2VA 分支配 `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16`），**无需额外节点**，本清单采用官方方案
- 文本编码器 NVFP4 官方实际大小 15.69GB（非社区文章所写 14.6GB）

---

## 二、目录落点（已完成/目标状态）

```
D:\Comfy-Desktop\ComfyUI-Shared\models\
├── diffusion_models\
│   └── minimax_h3_fl2va_pruned_int8_convrot.safetensors   ✅
├── text_encoders\
│   └── qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors        ✅
├── vae\
│   ├── minimax_h3_video_vae_int8_convrot.safetensors       ✅
│   └── minimax_h3_audio_vae_fp32.safetensors               ✅
└── loras\
    ├── minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors  ✅
    └── minimax_h3_flf2v_lora_v1.safetensors                ✅（可选）
```

已清理：`diffusion_models/`、`vae/`、`text_encoders/` 下 Wan 2.2 的 0 字节 `.part` 残留。

---

## 三、软件环境（已核实）

| 项 | 状态 |
|----|------|
| ComfyUI Desktop | `D:\Comfy-Desktop\`（v0.42.1 内核），模型目录指向 `ComfyUI-Shared\models\` |
| 内置 Python | 3.13.12 + **torch 2.12.1+cu130**（原生含 sm_120 内核，5050 无需任何 torch 改动） |
| 节点需求 | **零自定义节点**即可跑通（H3 原生支持已进 ComfyUI 主线 ≥v0.30，int8_convrot VAE 需 ≥v0.36，本版 0.42.1 满足） |
| 夸克残留 | `qwen3vl_32b_heretic_*.qkdownloading`（3.6GB 半截的第三方 heretic 变体）→ **不要用，建议手动删除** |

---

## 四、断点续传命令（若下载中断）

```bash
# 文本编码器 15.7GB
D:\LunaAI\tools\aria2-1.37.0-win-64bit-build1\aria2c.exe -x 8 -s 8 -c \
  --dir="D:\Comfy-Desktop\ComfyUI-Shared\models\text_encoders" \
  --out="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" \
  "https://hf-mirror.com/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"

# turbo 8步 LoRA 1.96GB
D:\LunaAI\tools\aria2-1.37.0-win-64bit-build1\aria2c.exe -x 8 -s 8 -c \
  --dir="D:\Comfy-Desktop\ComfyUI-Shared\models\loras" \
  --out="minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors" \
  "https://hf-mirror.com/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
```
（`-c` 断点续传；hf-mirror 已验证可达，HuggingFace 主站不必再试）

---

## 五、工作流与首跑参数（8GB 显存 + 16GB 内存保守档）

**入口**：ComfyUI → Workflow → Browse Templates → Video → **MiniMax H3**（选 T2V 模板先验证，再试 I2V）

| 参数 | 首跑值 | 说明 |
|------|--------|------|
| 模型选择 | `fl2va_pruned_int8_convrot` | FL2VA = 文生+图生视频；Ref2VA 是另一分支（参考图/视频/音频），您未下载 |
| 分辨率 | **832×480** | 720p 可跑但更慢；1080p 必 OOM 勿试 |
| 帧数 | **121 帧 ≈ 5 秒 @24fps** | 帧数÷24=秒数 |
| 步数 | 挂 turbo LoRA 后 **8 步**，CFG=1.0 | 不挂 LoRA 用 20 步 CFG=5.0；两者不要混 |
| 音频 | 首测**关闭**（generate_audio 关 / 不接 VAEDecodeAudio） | 跑通画面后再开 |
| turbo LoRA | `minimax_h3_fl2v_turbo_8step_v1.0`，LoraLoaderModelOnly 接 UNETLoader 后 | strength=1.0 |

**首跑前必做**：
1. 关闭浏览器等大内存程序（16GB 内存是瓶颈）
2. 停 Ollama（释放 bge-m3 显存）：`ollama stop` 或任务管理器结束 ollama 进程
3. Hunyuan3D 管线不要同时运行

**预期耗时**：5060 8GB 实测 720p 5s ≈ 9 分钟（20 步）；5050 更慢，480p 8 步估 4~8 分钟/条。

---

## 六、图生视频（I2V）要点

- 模板里用 `MiniMaxH3ImageToVideo` 节点，`first_frame` 接 LoadImage
- 也可以直接喂 **seedream 生成的图**做首帧，与您 or_proxy 生图链路衔接
- `minimax_h3_flf2v_lora_v1.safetensors`（首尾帧 LoRA）用于「给定首帧+尾帧生成中间过渡」，做连续分镜时才挂；与 turbo 8 步 LoRA **不要同时挂**

---

## 七、常见翻车点

| 症状 | 原因/解法 |
|------|-----------|
| 内存爆（非显存） | 16GB 硬伤，关程序；仍不行→加内存或降级（第八节） |
| 视频 OOM | 降分辨率 832×480→640×352，再砍帧数，再关音频 |
| 没声音 | audio VAE 未加载或缺 VAEDecodeAudio 节点；确认文件名无 `(1)` 残留 |
| VAE 格式不认 | ComfyUI < v0.36 不认 int8_convrot，需升级（本机 0.42.1 已满足） |
| 8 步跑出糊图 | CFG 忘了改 1.0，或 turbo LoRA 没挂/挂错分支（必须 FL2VA 配 FL2VA） |
| 报错找不到模型 | 模板默认填 BF16 文件名，手动改成清单里的量化版文件名（4 处：UNET/CLIP/两个 VAE） |

---

## 八、降级路径（若 16GB 内存确认跑不动 H3）

1. **首选降级**：Wan 2.2 TI2V-5B Q8 GGUF（5.4GB，`QuantStack/Wan2.2-TI2V-5B-GGUF`）——内存需求低得多（16GB 可跑 480p），但需另下 umt5_xxl 编码器（6.3GB）+ wan2.2_vae（1.3GB），共约 13GB 下载量
2. **次选**：FramePack F1（6GB 显存、内存压力小，专精图生视频，Windows 一键包）

---

## 九、后续接入小芸（可选）

ComfyUI Desktop 启动后自带 HTTP API（默认 `127.0.0.1:8188`）：`POST /prompt` 提交工作流 JSON + WebSocket 轮询进度。若要接进 or_proxy，仿照 seedream 端点模式加 `/v1/videos/generations` 异步转发即可。这一步等首跑验证通过后再做。


<!-- calib-20261105 -->
## 附：2026-11-05 状态说明

本文是本地 H3 部署清单，与桌宠主链路相互独立；「后续接入小芸（可选）」一节仍未实施。硬前提（物理内存 16GB）不变；页面文件已改 `D:\pagefile.sys 32768 65536`，**需重启操作系统生效**。ComfyUI 工作流约束（宽高必须都被 32 整除、`length` 满足 n%17==5）仍有效。
