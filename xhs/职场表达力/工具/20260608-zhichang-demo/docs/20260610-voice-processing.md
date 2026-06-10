# 声音生成与音色处理方案

## 技术方案

### TTS 引擎

使用 **Fish Audio** 克隆声音 API，通过 `fish-audio-sdk` 调用。

| 参数 | 值 |
|------|-----|
| Voice ID | `18a3192f9a684c16b60cf3880e6d0cce` |
| SDK | `fish-audio-sdk` |
| 语速控制 | `Prosody(speed=1.2)`（+20%，在模型端生成，音质优于 ffmpeg 拉伸） |

### 音色后处理（磁性效果）

生成 MP3 后，通过 ffmpeg 应用以下 filter chain：

```
equalizer=f=120:width_type=o:width=2:g=4,
equalizer=f=3500:width_type=o:width=2:g=2,
equalizer=f=7500:width_type=o:width=2:g=-3,
acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=2,
aecho=0.8:0.7:30:0.12
```

| 处理环节 | 参数 | 效果 |
|---------|------|------|
| 低频提升 | 120Hz +4dB | 声音更厚实、有分量 |
| 人声临场感 | 3500Hz +2dB | 听起来更近、更有穿透力 |
| 去硬（de-ess） | 7500Hz −3dB | 减少刺耳的齿音 |
| 动态压缩 | ratio=3, makeup=2dB | 动态更均匀，听起来更稳、更自信 |
| 微量混响 | delay=30ms, decay=0.12 | 增加空间感和深度 |

### 调参参考

如需进一步调整：
- **更低沉**：提高 120Hz 增益（+6dB）或加轻微降调 `asetrate=44100*0.97,atempo=1.03`
- **混响更强**：`aecho` decay 从 0.12 → 0.2，delay 从 30 → 50
- **混响更弱**：decay → 0.06，或直接删掉 `aecho` 一行
- **语速再快**：`Prosody(speed=1.3)` 或更高（建议不超过 1.5）

## 脚本

### S1 单段测试

```bash
python3 scripts/gen-tts-myvoice-s1.py
```

输出：`assets/test_vo1.wav`

### 全 10 段生成

```bash
python3 scripts/gen-tts-myvoice.py
```

输出：`assets/vo1.wav` ～ `assets/vo10.wav`，并打印各段时长，用于更新 `index.html` 时间轴。

> **注意**：`gen-tts-myvoice.py` 的 SDK 调用方式已在 S1 脚本中验证，运行前确认 Fish Audio 账号有余额。

## 测试视频

测试合成位于独立子目录，避免与主项目的 `index.html` 产生 `multiple_root_compositions` 冲突：

```
test/20260610-voice-test/
├── index.html       # S1 场景，14.5s 合成
├── package.json     # npm run render
└── assets/
    └── vo1.wav      # 克隆声音 + 磁性后处理
```

渲染命令：

```bash
cd test/20260610-voice-test
npm run render
# 输出: renders/20260610-voice-test.mp4
```
