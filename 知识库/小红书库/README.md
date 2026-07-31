# 小红书库

自动采集任务抓到的小红书高热笔记，沉淀为结构化知识原子。与 `原子库/`（dontbesilent 方法论）、`Eric原子库/`（Eric 自有观察）并列，这里存的是**市场信号**：什么选题、什么标题在职场表达/面试赛道真实拿到了流量。

## 与素材库的关系

`xhs/素材库/职场面试_记忆库.csv` 是采集任务的运行时数据盘（全量、每天增长、不入 git）。本库是它的**知识层提炼**：只收热度过线的笔记，去重、结构化、入 git 版本管理，供 skills 读取。

```
定时采集 → 记忆库.csv（全量数据，gitignore）
              ↓ sync.py（热度过滤 + 去重）
          小红书库/notes.jsonl（高热知识原子，入库）
              ↓ 被读取
eric-xhs-topic（选题评判参照）/ eric-xhs-title（标题公式验证）/ eric-xhs-audit（审核对标）
```

## notes.jsonl 格式

```json
{
  "id": "xhs_a1b2c3d4",
  "title": "面试官说回去等通知，其实是在等你这句话",
  "url": "https://www.xiaohongshu.com/...",
  "keyword": "面试潜台词",
  "source": "搜索-面试技巧",
  "first_seen": "2026-07-12",
  "heat": 370000,
  "heat_raw": "37万"
}
```

- `heat` 为数值化热度（"37万"→370000），入库门槛默认 ≥10000
- 去重键：url 优先，归一化标题兜底

## 同步

```bash
python3 知识库/小红书库/sync.py            # 默认门槛 1 万
python3 知识库/小红书库/sync.py --min-heat 50000
```

增量追加，重跑安全。建议每周同步一次并 commit，让 skills 的市场参照随账号赛道进化。
