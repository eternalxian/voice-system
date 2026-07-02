"""
莱德利基语音桥接 — 我把回复写入此文件，语音系统自动朗读

用法（莱德利基在回复后调用）：
  python voice_bridge.py "回复内容..."
  
或者管道输入：
  echo "回复内容" | python voice_bridge.py
"""

import sys
from pathlib import Path

BRIDGE_DIR = Path(__file__).parent / ".voice_bridge"  # E:\ai\.voice_bridge
BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = BRIDGE_DIR / "voice_output.txt"

if __name__ == "__main__":
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = sys.stdin.read()

    text = text.strip()
    if text:
        OUTPUT_FILE.write_text(text, encoding="utf-8")
        print(f"[桥接] 已写入 {len(text)} 字 → voice_output.txt")
    else:
        print("[桥接] 警告：输入为空")
