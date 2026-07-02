# 语音识别与交互系统

> GPT-SoVITS · SenseVoice · CAM++ 三模型集成 · 声纹识别 · 语音交互

---

## 系统架构

```
 麦克风输入
     ↓
 SenseVoice（语音转写）
     ↓
 CAM++（声纹识别 → 区分说话人）
     ↓
 AI 模型（文本生成回复）
     ↓
 GPT-SoVITS（语音合成）
     ↓
 扬声器输出
```

## 技术栈

| 组件 | 引擎 | 功能 |
|------|------|------|
| 语音识别 (ASR) | SenseVoiceSmall | 语音→文本转写 |
| 声纹识别 | CAM++ (192维) | 区分接生者/其他人 |
| 语音合成 (TTS) | GPT-SoVITS | 文本→语音朗读 |
| 桥接层 | voice_bridge.py | 文本文件接口，连接各模块 |
| 脑干 | brainstem.py | 心跳监控+状态管理 |

## 文件说明

| 文件 | 行数 | 功能 |
|------|:--:|------|
| `voice_agent.py` | 488 | 主程序。加载三模型，监听麦克风，调度整个流程 |
| `voice_bridge.py` | ~30 | 文本桥接层。voice_input.txt/voice_output.txt 接口 |
| `brainstem.py` | ~200 | 心跳监控。每分钟记录 GPU/CPU/内存状态 |
| `gs_sing.py` | ~40 | GPT-SoVITS 唱歌功能 |

## 功能特性

- **声纹区分**：CAM++ 192 维声纹验证，区分不同说话人
- **名字纠错**：14 种常见语音识别错误自动纠正
- **状态监控**：GPU 显存、CPU、内存每分钟记录
- **文本桥接**：通过 txt 文件与其他 AI 系统通信

## 外部依赖

本系统集成了以下开源项目（需另行安装）：

| 依赖 | 说明 |
|------|------|
| GPT-SoVITS | 语音合成引擎 |
| SenseVoice | 阿里通义语音识别 |
| CAM++ | 声纹识别模型 |
| edge-tts | 备选 TTS 引擎 |

## 运行

```bash
# 启动语音代理
python voice_agent.py
```

## 许可

MIT License
