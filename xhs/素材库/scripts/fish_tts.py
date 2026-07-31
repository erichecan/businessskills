#!/usr/bin/env python3
"""
Fish Audio TTS — 克隆人声合成
Usage: python fish_tts.py <voiceover_text_file> <output.mp3>
Config: 填写同目录 produce_config.env 中的 FISH_API_KEY / FISH_VOICE_ID
"""
import os, sys
from pathlib import Path

# 加载 produce_config.env
_cfg = Path(__file__).parent / "produce_config.env"
if _cfg.exists():
    for _line in _cfg.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

API_KEY  = os.environ.get("FISH_API_KEY", "")
VOICE_ID = os.environ.get("FISH_VOICE_ID", "")


def synthesize(text: str, output_path: str) -> None:
    if not API_KEY or API_KEY == "your_fish_audio_api_key_here":
        raise SystemExit("❌ FISH_API_KEY 未设置，请填写 scripts/produce_config.env")
    if not VOICE_ID or VOICE_ID == "your_cloned_voice_model_id_here":
        raise SystemExit("❌ FISH_VOICE_ID 未设置，请填写 scripts/produce_config.env")

    print(f"🎙️  正在合成语音（{len(text)} 字）...")

    # 优先用 SDK，降级用 HTTP
    try:
        from fish_audio_sdk import Session, TTSRequest
        session = Session(API_KEY)
        with session.tts(TTSRequest(reference_id=VOICE_ID, text=text)) as resp:
            with open(output_path, "wb") as f:
                for chunk in resp:
                    f.write(chunk)
    except ImportError:
        import requests
        r = requests.post(
            "https://api.fish.audio/v1/tts",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "reference_id": VOICE_ID,
                "format": "mp3",
                "latency": "normal",
            },
            stream=True,
            timeout=180,
        )
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)

    size_kb = Path(output_path).stat().st_size // 1024
    print(f"✅ 音频已保存: {output_path}  ({size_kb} KB)")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python fish_tts.py <text_file> <output.mp3>")
        sys.exit(1)
    _text = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    synthesize(_text, sys.argv[2])
