# 额度与模型分配

## 一、不要查额度

官方**没有**提供查询剩余免费额度的接口。已实测：

| 尝试 | 结果 |
|---|---|
| `compatible-mode/v1/models/quota` | 404 模型不存在 |
| `api/v1/account/quota`、`/api/v1/quota`、`/api/v1/usage` | 404 |
| `bailian.aliyuncs.com/v2/usage` | 200 但「API令牌认证失败」，DashScope Key 无法通过 |

额度初值只能来自下表 + 本地台账累加。

## 二、额度默认值（台账初值）

`K` = 1000 秒，`M` = 1,000,000 秒。

| 模型 | 额度 | 到期 | 档位 |
|---|---|---|---|
| `paraformer-v2` | 36K | 每月 1 日重置 · 长期有效 | free-monthly |
| `paraformer-8k-v2` | 36K | 每月 1 日重置 | free-monthly |
| `paraformer-realtime-v2` | 36K | 每月 1 日重置 | free-monthly |
| `fun-asr` | 36K | 2026-12-09 | free-once |
| `fun-asr-realtime` | 36K | 2026-12-09 | free-once |
| `qwen3-asr-flash-filetrans` | 36K | 2026-12-09 | free-once |
| `qwen-audio-3.0-asr-flash-filetrans` | 36K | 2026-12-09 | free-once |
| `qwen-audio-3.1-asr-flash-filetrans` | 1M | 2026-12-21 | free-once |
| **`qwen3-asr-flash`** | 36K | **2027-03-09** | **premium（禁用）** |

完整清单（含 TTS、多模态、向量模型）见项目内《当前百炼平台免费额度汇总》。

## 三、模型链

```
1. paraformer-v2                  36K/月，永久刷新，永远先用
2. fun-asr                        36K，2026-12-09
3. qwen3-asr-flash-filetrans      36K，2026-12-09
4. qwen-audio-3.0-asr-flash-filetrans  36K，2026-12-09
5. qwen-audio-3.1-asr-flash-filetrans  1M，2026-12-21（兜底大池）
—— 到此为止
```

**禁用** `qwen3-asr-flash`：2027-03 才到期的一次性额度，质量最高，属战略储备，仅用户点名可用。

**为什么 1M 的池子放最后**：额度大但 62 天后过期，属于"用不完也要浪费"的池子。先消耗临期的小额，再动大池，总浪费最小。

## 四、台账结构

`供AI查阅/quota-ledger.json`

```json
{
  "budgetCny": 5.0,
  "spentCny": 0.0,
  "models": [
    {
      "name": "paraformer-v2",
      "tier": "free-monthly",
      "freeQuotaSeconds": 36000,
      "usedSeconds": 0,
      "unitPriceCnyPerSecond": 0.00008,
      "expiresAt": null
    }
  ],
  "updatedAt": "2026-10-23T18:20:00+08:00"
}
```

每月 1 日把 `tier=free-monthly` 的 `usedSeconds` 归零。

## 五、分配：留 5% 余量

```
需要 S 秒
for m in chain:
    avail = (freeQuotaSeconds - usedSeconds) * 0.95
    take = min(S, avail)
    计费 += max(0, take - 剩余免费) * unitPrice
    S -= take
    if S <= 0: break
若 S > 0 → 全部链耗尽，报「还差 N 秒，需付费 X 元」
```

## 六、切换触发（任一即切，只向前不回退）

1. **预判**：`已用 + 剩余待处理 × 平均单条秒数 > 可用额度 × 0.95`
2. **实报**：错误含 `AllocationQuota` / `Throttling` / `Arrearage` / `额度` / `余额` / `欠费`
3. **异常**：同一模型连续 3 条非额度类失败

切换时对话报一行：`额度将尽，已切至 fun-asr（剩约 28 条），不影响进度`

**注意**：`task FAILED` 这类模型无关错误**不算**额度问题，不要触发切换。

## 七、预算熔断

| 触发 | 动作 |
|---|---|
| 开跑前预估 > 5 元 | 不启动，退回 G2 报告 |
| 执行中累计 ≥ 4 元（80%） | 暂停，报已花金额与剩余条数 |
| 触及 5 元 | 立即停止，未完成记 `pending` |
| 计费异常（金额跳变、欠费提示） | 立即停止 |

预算 5 元 ≈ 62,500 秒 ≈ 17.4 小时音频（全付费口径）。

## 八、必须提醒用户开启「用完即停」

控制台每个模型都有一个「用完即停」开关，默认**未开启**。

未开启时额度耗尽会**静默转按量付费**，没有任何提示，只能事后看账单。

G1 方案 MD 必须写：

```
额度保护：请确认百炼控制台「用完即停」已开启（当前状态：未开启）
```
