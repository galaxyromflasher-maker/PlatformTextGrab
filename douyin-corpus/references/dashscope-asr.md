# 百炼语音识别接口

## 一、五步调用

```
1) 取上传凭证
   GET https://dashscope.aliyuncs.com/api/v1/uploads?action=getPolicy&model=<model>

2) 上传音频（multipart/form-data）
   POST <policy.upload_host>

3) 提交异步任务
   POST https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription

4) 轮询
   GET https://dashscope.aliyuncs.com/api/v1/tasks/<task_id>

5) 取结果
   GET <results[0].transcription_url>
```

## 二、逐步骤细节

### 1. 取凭证

```
GET /api/v1/uploads?action=getPolicy&model=paraformer-v2
Authorization: Bearer <key>
```

返回 `data`：

```json
{
  "upload_host": "https://...",
  "upload_dir": "dashscope-instant/xxx",
  "oss_access_key_id": "...",
  "policy": "...",
  "signature": "...",
  "x_oss_object_acl": "private",
  "x_oss_forbid_overwrite": "true"
}
```

### 2. 上传

multipart 字段：

| 字段 | 值 |
|---|---|
| `OSSAccessKeyId` | `policy.oss_access_key_id` |
| `Signature` | `policy.signature` |
| `policy` | `policy.policy` |
| `key` | `<upload_dir>/<文件名>` |
| `x-oss-object-acl` | `policy.x_oss_object_acl` |
| `x-oss-forbid-overwrite` | `policy.x_oss_forbid_overwrite` |
| `success_action_status` | `200` |
| `file` | 音频文件 |

成功返回 200/203/204。上传后地址为 `oss://<key>`。

### 3. 提交任务

```
POST /api/v1/services/audio/asr/transcription
X-DashScope-Async: enable
X-DashScope-OssResourceResolve: enable
Content-Type: application/json

{
  "model": "paraformer-v2",
  "input": { "file_urls": ["oss://<key>"] },
  "parameters": { "language_hints": ["zh"], "disfluency_removal_enabled": false }
}
```

**参数差异**：

- `paraformer-v2`：用上面的 `parameters`
- 其余模型（`fun-asr`、`qwen3-asr-flash-filetrans`、`qwen-audio-3.1-asr-flash-filetrans` 等）：`"parameters": {}`

返回 `output.task_id`。

### 4. 轮询

每 3 秒一次，上限 10 分钟。

`task_status`：`PENDING` / `RUNNING` / `SUCCEEDED` / `FAILED` / `CANCELED` / `UNKNOWN`

`SUCCEEDED` 后检查 `output.results[0].subtask_status` 也必须是 `SUCCEEDED`，否则取 `results[0]` 里的错误信息。

### 5. 取结果

`transcription_url` 返回：

```json
{
  "properties": { "original_duration_in_milliseconds": 81387, "audio_format": "aac" },
  "transcripts": [
    {
      "text": "全文字符串",
      "sentences": [
        { "begin_time": 0, "end_time": 2400, "text": "第一句。" }
      ]
    }
  ]
}
```

`begin_time` / `end_time` 单位是**毫秒**。

## 三、错误处理

| 错误特征 | 含义 | 动作 |
|---|---|---|
| 含 `AllocationQuota` / `Throttling` / `Arrearage` / `额度` / `余额` / `欠费` | 额度耗尽或限流 | 切下一个模型 |
| `task_status = FAILED`，且换模型仍失败 | 多为纯音乐/无语音 | 记 `no_speech`，不重试 |
| HTTP 403 / 404（下载阶段） | 直链过期 | 重新滚动主页刷新直链，重试一次 |
| 轮询超时 | 服务异常 | 记 `failed`，继续下一条 |

## 四、并发与限速

| 项 | 值 |
|---|---|
| 下载并发 | 3 |
| 下载间隔 | 0.5–1.5 秒随机 |
| 下载超时 | 120 秒 |
| 转写轮询上限 | 600 秒 |
| 单条重试 | 2 次（退避 3s / 9s） |

异步任务本身可并发提交，轮询是轻量 GET。

## 五、成本系数

`paraformer-v2` 单价 0.00008 元/秒（免费额度内为 0）。

按实测，端到端（含上传与排队）约 20–27 倍实时：12.5 小时音频约 15–20 分钟跑完。
