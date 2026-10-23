# 目录结构与交付规范

## 一、目录（脚本自动生成）

```
<当前项目>/
└─ 抖音博主文案提取/
   ├─ <昵称>-<抖音号>/
   │  ├─ STATUS.md              唯一状态文件，人读的第一入口
   │  ├─ G1-方案.md
   │  ├─ G2-清单.md
   │  ├─ G3-转写完成.md
   │  ├─ 清单/
   │  │  ├─ works.csv
   │  │  └─ works.xlsx
   │  ├─ 转写/<序号>_<作品ID>/...
   │  └─ 供AI查阅/
   │     ├─ README.md
   │     ├─ plan.json
   │     ├─ env-check.json
   │     ├─ login-state.json
   │     ├─ sample-probe.json
   │     ├─ manifest-raw.json
   │     ├─ checks.json
   │     ├─ preflight-g3.json
   │     ├─ quota-ledger.json
   │     ├─ transcribe-summary.json
   │     └─ run-log.jsonl
   └─ <第二个博主>-<抖音号>/
```

规则：

- 根目录固定 `抖音博主文案提取/`，多博主并列
- 博主子目录 = `昵称-抖音号`，防重名
- 不询问输出路径
- `供AI查阅/` 人不必看；对话里不贴 JSON，MD 里不指向 JSON

## 二、Gate 交付（每个 Gate 只给两件）

| 交付 | 形态 | 要求 |
|---|---|---|
| 一页表格 | csv / xlsx 路径 | 不超过 20 行，超出按类别拆 |
| 一个 MD | 项目内 md | 固定六段 |

### MD 六段骨架

```markdown
# G2 清单 · <博主名>

结论：<一句话结论，含关键数字>

## 范围
条数、构成、时间跨度、音频时长

## 依据
对账结果、抽样实测、倍速实测

## 风险
异常条目、登录限制、图文占比

## 已排除
本轮明确不做的事

## 下一步
回复「确认」开始转写
```

模板见 `assets/gate-template.md`。

## 三、STATUS.md（四行，固定）

```
阶段：G2 待确认
待确认：清单与额度分配
下一步：回复「确认」开始转写
更新：2026-10-23 18:20
```

## 四、清单字段

`类别 / 序号 / 作品ID / 标题 / 发布日期 / 时长 / 点赞 / 评论 / 收藏 / 转发 / 类型`

- 排序：发布时间由新到旧
- Excel：冻结首行、开筛选、列宽适中
- 类型：`视频` / `图文`

## 五、逐条转写产物

```
转写/<序号>_<作品ID>/
├─ audio.m4a          音轨，保留
├─ transcript.json    模型原始返回，不加工
├─ text.txt           一句一行
├─ text.md            分段版，行首 [起–止]
└─ meta.json          状态与耗时
```

### meta.json 关键字段

```json
{
  "order": 13,
  "awemeId": "7660476565372931364",
  "title": "标题原文",
  "date": "2026-09-25",
  "durationMs": 81387,
  "audioDuration": 81.5,
  "model": "paraformer-v2",
  "status": "ok",
  "error": null,
  "timings": { "downloadSeconds": 2.8, "extractSeconds": 0.3, "asrSeconds": 7.3, "totalSeconds": 10.4 },
  "sentences": 26,
  "paragraphs": 8,
  "chars": 1188
}
```

`status`：`ok` / `no_speech` / `failed` / `pending`

### text.md 分段规则

- 按标点（。！？）合并 2–5 句，90–200 字一段
- 行首 `[起–止]`，短横线是 **U+2013**，不是减号
- 末尾一行 `_音频总长 mm:ss_`
- **不改字**，只分段与加时间戳

## 六、G3 交付

**表格**：`转写/转写结果.csv`

`序号 / 作品ID / 类别 / 标题 / 时长 / 状态 / 字数 / 模型 / 耗时`

**MD**：`G3-转写完成.md`，六段，其中「依据」段必须含费用结算表：

| 模型 | 处理秒数 | 计费 | 说明 |
|---|---|---|---|
| paraformer-v2 | 36,000 | ¥0 | 月度免费额度 |
| fun-asr | 9,039 | ¥0 | 一次性额度 |
| 合计 | 45,039 | **¥0** | — |

## 七、`供AI查阅/README.md` 必须说明

逐个文件写清：文件名、内容、谁读、什么时候读。例如：

```
plan.json            G1 确认的方案参数（模型、预算、并发、范围），G2/G3 开始前读
quota-ledger.json    额度台账，每次调用前读、调用后写
run-log.jsonl        逐条执行日志，排查失败时读
manifest-raw.json    清单原始抓取数据，重建 works.csv 时读
```
