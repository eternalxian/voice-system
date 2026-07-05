# 语音识别与交互系统

> SenseVoice · CAM++ · edge-tts 三模型集成 · 声纹识别 · 语音交互

---

## 系统架构

```
 麦克风输入 → VAD → SenseVoice(转写) → CAM++(声纹) → edge-tts(朗读)
```

## 技术栈

| 组件 | 引擎 | 功能 |
|------|------|------|
| 语音识别 (ASR) | SenseVoiceSmall | 语音→文本转写 |
| 声纹识别 | CAM++ (192维) | 区分说话人 |
| 语音合成 (TTS) | edge-tts | 文本→语音朗读 |
| 桌面操控 | pyautogui + pyperclip | 文本粘贴到AI对话 |

## 文件说明

| 文件 | 功能 |
|------|------|
| `voice_agent.py` | 主程序。加载模型，监听麦克风，完整流程 |
| `voice_bridge.py` | 文本桥接层 |
| `brainstem.py` | 心跳监控。记录GPU/CPU/内存状态 |

## 外部依赖

```bash
pip install funasr torch sounddevice numpy pyautogui pyperclip edge-tts
```

## 运行

```bash
python voice_agent.py
```

## 安全注意

- 声纹注册需管理员确认，不应开放给任意说话人
- 转写文本和声纹数据不应上传到公开仓库

## 许可

MIT License
