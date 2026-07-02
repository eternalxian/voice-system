"""
莱德利基语音系统 v7 — 声纹识别版

架构：
  麦克风 → VAD → SenseVoice 转写 → CAM++ 声纹验证
    → 识别说话人 → 标注 [接生者]/[其他人]
    → Ctrl+V 粘贴 → 莱德利基回复 → edge-tts 朗读
"""

import sys, time, queue, re, wave, tempfile, os
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
import torch
import sounddevice as sd
from funasr import AutoModel
import pyautogui
import pyperclip
import pygetwindow as gw
import win32com.client
import keyboard

# ─── 配置 ───────────────────────────────────────────

SAMPLE_RATE = 16000
BLOCK_SIZE = 512
SILENCE_THRESHOLD = 0.25  # 嘈杂派对房，提高阈值减少他人误触发
SILENCE_DURATION = 1.2
MIN_SPEECH_DURATION = 0.4
MAX_SPEECH_DURATION = 30.0
VOICEPRINT_THRESHOLD = 0.35  # 余弦相似度阈值（远程音频波动大，稍降低）
REPEAT_MODE = True  # 复述模式：True=打字直接朗读，False=正常对话
WAKE_FILTER = True  # 唤醒词过滤——接生者重新开启

# 转写纠错词典：常见误识别 → 正确写法
CORRECTIONS = {
    "来的立即": "莱德利基",
    "来德立即": "莱德利基",
    "来的立基": "莱德利基",
    "来德利基": "莱德利基",
    "來的立即": "莱德利基",
    "來德立即": "莱德利基",
    "來的歷經": "莱德利基",
    "来的历经": "莱德利基",
    "來得立即": "莱德利基",
    "来得立即": "莱德利基",
    "来德利其": "莱德利基",
    "来的第七": "莱德利基",
    "陸來的立即": "莱德利基",
    "陆来的立即": "莱德利基",
}

BRIDGE_DIR = Path(__file__).parent / ".voice_bridge"  # E:\ai\.voice_bridge
BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = BRIDGE_DIR / "voice_output.txt"
INPUT_FILE = BRIDGE_DIR / "voice_input.txt"
STATUS_FILE = BRIDGE_DIR / "voice_status.txt"
VOICEPRINT_FILE = BRIDGE_DIR / "voiceprint.npy"

# ─── 初始化 ──────────────────────────────────────────

vad_model = None
asr_model = None       # SenseVoice
sv_model = None        # CAM++ 声纹
voiceprint = None      # 注册的声纹向量
last_spoken = ""
output_mtime = 0

# 在 VoiceRecorder 之前定义 audio_queue
audio_queue = queue.Queue()


def load_vad():
    global vad_model
    if vad_model is None:
        from silero_vad import load_silero_vad
        vad_model = load_silero_vad()
        print("[语音] VAD 就绪")
    return vad_model


def load_asr():
    global asr_model
    if asr_model is None:
        print("[语音] 加载 SenseVoice...")
        asr_model = AutoModel(
            model="iic/SenseVoiceSmall",
            trust_remote_code=True, device="cuda:0", disable_update=True,
        )
        print("[语音] SenseVoice 就绪 (GPU)")
    return asr_model


def load_sv():
    global sv_model
    if sv_model is None:
        print("[语音] 加载 CAM++ 声纹模型...")
        sv_model = AutoModel(
            model="iic/speech_campplus_sv_zh-cn_16k-common",
            trust_remote_code=True, device="cuda:0", disable_update=True,
        )
        print("[语音] CAM++ 就绪 (GPU)")
    return sv_model


# ─── 声纹 ────────────────────────────────────────────

def load_voiceprint():
    global voiceprint
    if VOICEPRINT_FILE.exists():
        voiceprint = np.load(str(VOICEPRINT_FILE))
        print(f"[语音] 已加载声纹 ({len(voiceprint)}维)")
        return True
    return False


def save_voiceprint(embedding: np.ndarray):
    global voiceprint
    voiceprint = embedding.flatten()
    np.save(str(VOICEPRINT_FILE), voiceprint)
    print(f"[语音] 声纹已保存 ({voiceprint.shape[0]}维)")


def extract_embedding(wav_path: str) -> np.ndarray | None:
    """用 CAM++ 提取音频的声纹嵌入"""
    sv = load_sv()
    try:
        result = sv.generate(input=wav_path)
        if result and len(result) > 0:
            emb = result[0].get("spk_embedding")
            if emb is not None:
                # CAM++ 返回 torch tensor，先移回 CPU 再转 numpy
                if hasattr(emb, 'cpu'):
                    emb = emb.cpu()
                if hasattr(emb, 'numpy'):
                    emb = emb.numpy()
                return np.array(emb).flatten()
    except Exception as e:
        print(f"[声纹] 提取失败: {e}")
    return None


def verify_speaker(embedding: np.ndarray) -> tuple[bool, float]:
    """比较嵌入与注册声纹的相似度"""
    global voiceprint
    if voiceprint is None:
        return False, 0.0
    # 余弦相似度
    sim = np.dot(embedding, voiceprint) / (
        np.linalg.norm(embedding) * np.linalg.norm(voiceprint) + 1e-8
    )
    return sim > VOICEPRINT_THRESHOLD, sim


def register_voiceprint():
    """录制一段音频注册声纹"""
    print("\n[声纹] === 声纹注册 ===")
    print("[声纹] 请说一段 3-5 秒的话，比如'莱德利基你好我是接生者'")
    print("[声纹] 录音中...")

    audio = sd.rec(int(5 * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=1, dtype=np.float32)
    sd.wait()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes((audio.flatten() * 32767).astype(np.int16).tobytes())

    emb = extract_embedding(wav_path)
    os.unlink(wav_path)

    if emb is not None:
        save_voiceprint(emb)
        print("[声纹] 注册成功！")
        return True
    else:
        print("[声纹] 注册失败，请重试")
        return False


# ─── 状态 ────────────────────────────────────────────

def write_status(s: str):
    try:
        STATUS_FILE.write_text(s, encoding="utf-8")
    except Exception:
        pass


# ─── TTS ─────────────────────────────────────────────

def speak(text: str):
    global last_spoken
    text = text.strip()
    if not text or text == last_spoken:
        return
    if last_spoken and text.startswith(last_spoken):
        new = text[len(last_spoken):].strip()
        if not new:
            return
        text = new
    last_spoken = text[-800:]

    text = re.sub(r'[*_~`#>\-\[\]\(\)\|]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) < 3:
        return

    print(f"\n[朗读] ({len(text)}) {text[:80]}...")
    try:
        import subprocess as sp
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            mp3_path = f.name
        sp.run([
            "edge-tts", "--voice", "zh-CN-XiaoxiaoNeural",
            "--text", text, "--write-media", mp3_path,
        ], capture_output=True, timeout=30)
        if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
            # 通过网易虚拟扬声器播放——远程桌面可听到
            wav_path = mp3_path.replace(".mp3", ".wav")
            sp.run(["ffmpeg", "-y", "-i", mp3_path, "-acodec", "pcm_s16le", wav_path],
                   capture_output=True, timeout=15)
            if os.path.exists(wav_path):
                import soundfile as sf
                audio, sr = sf.read(wav_path)
                sd.play(audio, sr)   # 系统默认输出——远程桌面自动路由
                sd.wait()
                os.unlink(wav_path)
        os.unlink(mp3_path)
    except Exception as e:
        print(f"[语音] TTS 失败: {e}")


# ─── 终端粘贴 ────────────────────────────────────────

def find_and_focus_terminal():
    titles = ["Reasonix", "Windows Terminal", "Terminal"]
    for t in titles:
        windows = gw.getWindowsWithTitle(t)
        if windows:
            try:
                windows[0].activate()
                time.sleep(0.15)
                return True
            except Exception:
                continue
    try:
        x, y = pyautogui.position()
        pyautogui.click(x, y)
        time.sleep(0.1)
        return True
    except Exception:
        return False


def paste_text(text: str):
    if not find_and_focus_terminal():
        print("[语音] 警告: 未能聚焦终端")
    pyperclip.copy(text)
    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    pyautogui.press("enter")
    print(f"[语音] 已发送: {text}")


# ─── 音频录制 ────────────────────────────────────────

class VoiceRecorder:
    def __init__(self):
        self.stream = None

    def audio_callback(self, indata, frames, time_info, status):
        if status:
            pass
        audio_queue.put(indata.copy().flatten())

    def start(self):
        preferred = ["NVIDIA Broadcast", "RTX-Audio", "主声音捕获驱动程序", "Microsoft 声音映射器", "麦克风阵列"]
        device_id = None
        for keyword in preferred:
            for i, d in enumerate(sd.query_devices()):
                if keyword in d["name"] and d["max_input_channels"] > 0:
                    try:
                        # 实测信号，选有声音的设备
                        audio = sd.rec(int(0.5 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, device=i, dtype='float32')
                        sd.wait()
                        peak = np.max(np.abs(audio))
                        if peak < 0.005:
                            continue  # 无信号，跳过
                        sd.check_input_settings(device=i, samplerate=SAMPLE_RATE, channels=1)
                        device_id = i
                        print(f"[语音] 使用设备 [{i}]: {d['name']} (信号={peak:.4f})")
                        break
                    except Exception:
                        continue
            if device_id is not None:
                break

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK_SIZE,
            callback=self.audio_callback, dtype=np.float32, device=device_id,
        )
        self.stream.start()
        print("[语音] 麦克风就绪")

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()

    def drain(self):
        while not audio_queue.empty():
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                break

    def listen_and_transcribe(self) -> tuple[str | None, np.ndarray | None, str | None]:
        """返回 (转写文本, 音频数据, wav路径)"""
        model = load_vad()
        recording = []
        in_speech = False
        silence_frames = 0
        speech_frames = 0
        silence_limit = int(SILENCE_DURATION * SAMPLE_RATE / BLOCK_SIZE)
        min_frames = int(MIN_SPEECH_DURATION * SAMPLE_RATE / BLOCK_SIZE)
        max_frames = int(MAX_SPEECH_DURATION * SAMPLE_RATE / BLOCK_SIZE)

        while True:
            try:
                chunk = audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            tensor = torch.from_numpy(chunk[:512].copy()).float()
            prob = model(tensor, SAMPLE_RATE).item()

            if prob > SILENCE_THRESHOLD:
                if not in_speech:
                    in_speech = True
                    recording = []
                    speech_frames = 0
                    write_status("hearing")
                recording.append(chunk)
                speech_frames += 1
                silence_frames = 0
                if speech_frames >= max_frames:
                    break
            elif in_speech:
                recording.append(chunk)
                silence_frames += 1
                if silence_frames >= silence_limit:
                    break

        if speech_frames < min_frames:
            return None, None, None

        write_status("transcribing")
        audio = np.concatenate(recording)

        # 保存 WAV
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes((audio * 32767).astype(np.int16).tobytes())

        # 转写
        asr = load_asr()
        text = None
        try:
            result = asr.generate(input=wav_path, language="zh")
            if result and len(result) > 0:
                raw = result[0].get("text", "").strip()
                raw = re.sub(r'<\|[^|]*\|>', '', raw).strip()
                if raw:
                    text = raw
                    # 纠错：替换常见误识别
                    for wrong, correct in CORRECTIONS.items():
                        if wrong in text:
                            text = text.replace(wrong, correct)
                    print(f"[语音] 转写: {text}")
        except Exception as e:
            print(f"[语音] ASR 错误: {e}")

        if text is None:
            os.unlink(wav_path)
            return None, None, None

        return text, audio, wav_path


# 过滤开关
filter_enabled = True  # F9 切换

def on_f9(_=None):
    global filter_enabled
    filter_enabled = not filter_enabled
    status = "开" if filter_enabled else "关"
    print(f"\n[语音] 声纹过滤: {status}")
    try:
        import pythoncom
        pythoncom.CoInitialize()
        speaker.Speak(f"声纹过滤已{status}启", 0)
    except Exception:
        pass

# ─── 文件监控 ────────────────────────────────────────

def check_output():
    global output_mtime
    try:
        if OUTPUT_FILE.exists():
            mt = OUTPUT_FILE.stat().st_mtime
            if mt > output_mtime:
                output_mtime = mt
                return OUTPUT_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None


# ─── 主循环 ──────────────────────────────────────────

def main():
    global voiceprint, REPEAT_MODE, WAKE_FILTER
    print("=" * 55)
    print("  莱德利基语音系统 v7 — 声纹识别版")
    print(f"  桥接: {BRIDGE_DIR}")
    print("  说话 → 转写 → 声纹验证 → 粘贴 → 回复 → 朗读")
    print("  F9 = 开关过滤  |  Ctrl+C = 退出")
    print("=" * 55)

    load_vad()
    load_asr()
    load_sv()

    # TTS 使用 edge-tts 晓晓女声，快速稳定
    print("[语音] TTS: edge-tts 晓晓 (女声)")

    # 加载声纹（不自动注册，稍后通过说注册短语来注册）
    has_voiceprint = load_voiceprint()
    if not has_voiceprint:
        print("[声纹] 未注册，将以无验证模式运行")
        print("[声纹] 要说注册短语'莱德利基注册声纹'来注册")

    keyboard.on_press_key("F9", lambda _: (
        speak(OUTPUT_FILE.read_text(encoding="utf-8").strip())
        if OUTPUT_FILE.exists() else None
    ))

    recorder = VoiceRecorder()
    recorder.start()

    global output_mtime
    if OUTPUT_FILE.exists():
        output_mtime = OUTPUT_FILE.stat().st_mtime

    write_status("listening")
    print("[语音] 就绪\n")

    try:
        while True:
            write_status("listening")
            
            # 在监听时也检查桥接——支持直接复述（仅在复述模式下）
            if REPEAT_MODE:
                direct_resp = check_output()
                if direct_resp:
                    print(f"\n[朗读] ({len(direct_resp)}) {direct_resp[:80]}...")
                    write_status("speaking")
                    speak(direct_resp)

            text, audio, wav_path = recorder.listen_and_transcribe()

            if text is None:
                continue

            # 检测注册短语（可多次注册，取平均值）
            if "注册声纹" in text and wav_path:
                print("[声纹] 检测到注册短语，提取声纹...")
                emb = extract_embedding(wav_path)
                if emb is not None:
                    if voiceprint is not None:
                        # 多次注册取平均，声纹更稳定
                        voiceprint = (voiceprint + emb) / 2.0
                        np.save(str(VOICEPRINT_FILE), voiceprint)
                        print(f"[声纹] 更新成功! 累计注册，维度={len(voiceprint)}")
                    else:
                        voiceprint = emb
                        np.save(str(VOICEPRINT_FILE), voiceprint)
                        print(f"[声纹] 初次注册成功! 维度={len(voiceprint)}")
                    speaker_label = "[接生者]"
                else:
                    print("[声纹] 提取失败")

            # 声纹验证
            speaker_label = ""
            if wav_path and voiceprint is not None and "注册声纹" not in text:
                emb = extract_embedding(wav_path)
                if emb is not None:
                    is_match, sim = verify_speaker(emb)
                    speaker_label = "[接生者]" if is_match else "[其他人]"
                    print(f"[声纹] 相似度={sim:.3f} → {speaker_label}")

            # 清理 WAV
            try:
                os.unlink(wav_path)
            except Exception:
                pass

            # 提示词开关：关闭提示词=所有声音放行
            if "关闭提示词" in text or "所有声音进来" in text:
                WAKE_FILTER = False
                print("[语音] 提示词已关闭——所有声音放行")
                continue
            if "开启提示词" in text or "过滤声音" in text or "开启唤醒词" in text:
                WAKE_FILTER = True
                print("[语音] 提示词已开启——只放行唤醒词")

            # 唤醒词过滤：只放行以"莱德利基"等开头的语音，不判断说话人
            if WAKE_FILTER:
                WAKE_WORDS = ["莱德利基", "来的立机", "来德利基", "來的立機", "萊德利基", "来的立即", "莱德立即", "来的立基", "來得立即", "来得立即", "来的立", "来德立", "來的立", "兰德利基", "列的立基", "的立基", "来的历", "来德立基", "莱德力基", "白德利基", "爱德利基", "列的力基", "利基"]
                is_wake = any(text.startswith(w) for w in WAKE_WORDS)
                if not is_wake:
                    print(f"[语音] 拦截(无唤醒词): {text[:50]}...")
                    continue

            # 组合标注
            full_text = f"{speaker_label} {text}" if speaker_label else text
            INPUT_FILE.write_text(full_text, encoding="utf-8")

            write_status("sending")
            paste_text(full_text)

            # 复述模式切换
            if "停止复述" in text and speaker_label == "[接生者]":
                REPEAT_MODE = False
                print("[复述] 已关闭复述模式")
                continue
            if "开始复述" in text and speaker_label == "[接生者]":
                REPEAT_MODE = True
                print("[复述] 已开启复述模式")
                continue

            # 注册声纹的语音不等待回复，立即继续监听
            if "注册声纹" in text:
                print("[声纹] 注册完成，继续监听中...")
                time.sleep(0.5)
                continue

            write_status("waiting")
            print("[语音] 等待回复...", end="", flush=True)
            waited = 0
            while waited < 15:
                time.sleep(0.5)
                waited += 0.5
                resp = check_output()
                if resp:
                    print(f" ({waited:.0f}s)")
                    write_status("speaking")
                    speak(resp)
                    break
                if waited % 10 == 0:
                    print(".", end="", flush=True)
            else:
                print(" 超时")

            write_status("cooldown")
            time.sleep(2.0)
            recorder.drain()

    except KeyboardInterrupt:
        print("\n[语音] 退出...")
    finally:
        recorder.stop()
        write_status("stopped")
        print("[语音] 再见")


if __name__ == "__main__":
    main()
