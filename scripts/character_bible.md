# 角色设定档 — 中年男主角

## 核心形象（已通过 test_chibi_03.png 验证）

- 年龄感：40-45岁，有明显沧桑感
- 脸型：偏瘦的椭圆脸，非胖圆脸
- 面部细节：鱼尾纹、法令纹、黑眼圈、胡茬（未刮净的青胡茬）、鬓角白发
- 眼神：疲惫、沉默、有心事，不是愤怒，不是开心
- 发型：黑发带灰，略凌乱

## 头身比要求

- 目标比例：1:2（写实漫画/韩漫风格）
- 禁止：chibi 1:1 大头娃娃比例

## 风格关键词

- 2D flat anime / Korean manhwa webtoon style
- NOT chibi, NOT Q版 big-head
- 线条简洁但情绪表达丰富

## 固定场景元素（夜晚沙发场景）

- 深色沙发，夜晚室内
- 手机屏幕光打在脸上
- 白色衬衫，略有褶皱
- 冷蓝色 + 深紫色环境光

## 参考图

- `test_images/test_chibi_03.png` — 面部已通过，比例待修正
- `test_images/test_chibi_04.png` — 目标：面部保持 + 比例修正

## Codex 生成指令模板

生成新场景时，必须带上 `-i test_chibi_03.png` 作为角色参考，并在 prompt 中强调：

```
Keep the SAME character face from the reference image: weathered middle-aged Chinese man,
slim oval face, crow's feet, gray temples, stubble, tired eyes, black hair with gray.
Head-to-body ratio approximately 1:2 (manhwa/webtoon style, NOT chibi).
[在此描述新场景]
2D flat anime/webtoon art style. Vertical 9:16 composition.
```
