---
name: douyin-corpus
description: This skill should be used when the user wants to extract full spoken-content transcripts from a Douyin (TikTok China) creator's video account — "把这个博主的视频转成文字", "扒某个抖音号的口播文案", "给我做这个号的语料库". It walks three gated rounds (probe & plan → manifest → transcription), keeps cost under a hard budget, auto-switches ASR models before quota runs out, and produces machine-readable manifests plus a human-readable gate report at each stop.
---

# 抖音博主语料采集

把一个抖音博主的全部作品转成可检索的口播文字，成本可控，全程只有三次需要人确认。

## 三条铁律

1. **到 Gate 必须停**。交一页表格 + 一个 MD，等用户回复「确认」。不得用"接下来我将…"自行推进。
2. **不超预算、不用尽模型额度、不碰最高档模型**。估算超预算就停下来报，不自行决定。
3. **人类入口只给表格和 MD**。所有 JSON、原始返回、日志收进 `供AI查阅/`，人不必看。

## 三轮流程

| 轮次 | 目标 | 交付 | 确认词 |
|---|---|---|---|
| G1 | 收集信息 + 实测 + 出方案 | 方案 MD（六段） | 「确认」开始取清单 |
| G2 | 取清单 + 对账 + 分配额度 | 清单 xlsx + 清单 MD | 「确认」开始转写 |
| G3 | 全量转写 | 结果 csv + 完成 MD | 无（本轮结束） |

目录结构、交付细节、字段规范见 `references/deliverables.md`。

---

## 第一轮：G1 方案

### 只问两件事

- 博主主页链接 **或** 抖音号（二选一）
- 语音识别 Key 怎么给：**A 直接粘贴**（临时 Key，不落盘、不回显）或 **B 给文件路径**

其余全部用默认值，写在方案 MD 里让用户否决，不主动询问：预算 5 元、并发 3、音频保留、图文不转写、模型自动分配。

### 必须当场做完的事

1. 环境体检：执行 `python scripts/probe_env.py`
2. 建项目：执行 `python scripts/init_project.py --creator-name <昵称> --creator-id <抖音号>`
3. 扫码登录，**当场验证是否突破 21 条限制**（未登录主页只放行约 21 条）
4. 抽 3 条（短/中/长）取直链、各下载 1 条验证可用性
5. 用其中 1 条真实音频跑一次 ASR，得到**实测倍速**（不得用理论值代替）
6. 算账 → 写 `G1-方案.md` → 更新 `STATUS.md` → **停**

### 登录未突破 21 条

停在 G1，明确告知"登录未生效，无法取全量"，给出重新扫码选项。**未验证全量不得进入第二轮。**

### 浏览器层（内置，不必现搭）

```bash
python scripts/browser.py check                      # 检查 playwright + Edge
python scripts/browser.py login --profile <目录>      # 开有窗口的 Edge 扫码，成功后自动关窗
python scripts/browser.py resolve --id <抖音号>       # 抖音号 -> 主页 URL
python scripts/browser.py capture --url <主页URL> --out raw.json --profile <目录>
```

三条硬规则：

1. **不下载 Chromium**，用 `channel="msedge"` 复用本机 Edge
2. **二维码不截图**——异步渲染，截到的是占位图。开有窗口的浏览器让用户自己扫
3. **登录成功会主动关窗**，这是正常行为，需提前告知用户，否则会被当成崩溃

登录态存在 `_browser-profile/`，跨命令复用，**不要删除、不要外传**。

---

## 第二轮：G2 清单

1. 打开主页，滚动到底（容器是 `div.parent-route-container`，不是 window）
2. 注入 `assets/capture.js` 捕获作品列表，导出原始 JSON
3. 执行 `python scripts/build_manifest.py --project <目录> --raw <json> --page-total <页面显示数>`
4. 核对三项：抓取数 / 页面显示数 / 去重后数
5. 与已有清单做增量差集（新增 / 跳过 / 下线）
6. 按 `references/quota-and-models.md` 分配额度，留 5% 余量
7. 出 `清单/works.xlsx` + `G2-清单.md` → **停**

预估费用超预算时，停在 G2 写明差额，不得开跑。

---

## 第三轮：G3 转写

1. 开跑前检查：台账一致性、直链有效性、磁盘空间、待处理条数
2. 执行 `python scripts/transcribe.py --project <目录> --workers 3`
3. 脚本自动处理：模型链切换、额度预判、预算熔断、失败重试、断点续跑、音轨完整性校验
4. **原片默认保留**（`--cleanup-mp4` 才边跑边删）。大批量逐条删除会触发宿主保护，跑完统一清理
5. 代码缺陷类异常（KeyError 等）会立即中止并打印堆栈，不参与重试熔断
4. 每 20 条或每 3 分钟报一行进度
5. 收尾出 `转写结果.csv` + `G3-转写完成.md` → **结束**

**本轮结束即停**，不自动进入审核，不自动生成阅读包。用户回复「审核」才继续。

模型链、切换条件、熔断阈值见 `references/quota-and-models.md`。
百炼接口调用细节见 `references/dashscope-asr.md`。

---

## 已验证的事实（不要重复试错）

| 事实 | 结论 |
|---|---|
| 未登录主页只放行约 21 条 | 必须登录 |
| 滚动容器 `div.parent-route-container` | `window.scrollTo` 无效 |
| `play_addr` 直链 | 带 UA + `Referer: https://www.douyin.com/` 可裸下，5–6 MB/s，不需要 cookie 签名 |
| **yt-dlp 下载抖音** | 新旧版本带 cookie 均 403，**不要用** |
| 一次主页滚动 | 可拿到全部作品的完整字段与直链，无需逐条请求详情接口 |
| 想只留音频 | 也必须下完整 mp4，再抽音轨、删原片 |
| **查询剩余额度接口** | **官方未提供**，实测全部 404，不要尝试 |
| **逐条删原片** | 宿主对单轮删除有阈值（约 50 个），超过会杀进程。默认保留原片，收尾统一清理 |
| **`[data-e2e=user-tab-count]`** | 常返回 null，需从「作品 1355」「1.4万」这类文本正则兜底 |
| **页面数 > 抓取数** | 差值多为私密/审核中/已下架。连抓两次比对 ID 集合，两次一致即可判定非加载遗漏 |

## 禁止事项

1. 到 Gate 不停、自行推进
2. 额度耗尽后硬跑，或切到 `qwen3-asr-flash`（premium 档）
3. 超预算自行决定继续
4. Key 写入文件、写入日志、在输出中回显
5. 上传音频到未配置的第三方服务
6. 删除音频文件
7. 因单条失败中断整批
8. 对话里贴 JSON、MD 里指向 JSON
9. 使用 yt-dlp 下载抖音
10. 尝试查询额度接口
11. 在长任务里逐条删原片（触发宿主批量删除保护，会杀进程）
12. 把代码缺陷类异常（KeyError / TypeError / AttributeError）当成业务失败重试

## 参数默认值

| 参数 | 值 |
|---|---|
| 预算 | 5 元 |
| 并发 | 3 |
| 下载间隔 | 0.5–1.5 秒随机 |
| 音频保留 | 是 |
| 额度余量 | 5% |
| 预算暂停线 | 80% |
| 熔断阈值 | 连续 5 条同类失败 |
| 进度汇报 | 每 20 条或 3 分钟 |
| 时间戳短横线 | U+2013 `–` |
| CSV 编码 | UTF-8 with BOM |
