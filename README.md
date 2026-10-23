# PlatformTextGrab

A CodeBuddy skill package that turns a creator's video account into a searchable text corpus.

It walks three gated rounds — plan, manifest, transcription — and stops for human confirmation at each gate. Cost is capped by a hard budget; speech-recognition models are rotated automatically before any free quota runs out.

## What it does

1. **Plan** — collects the account link and an API key, verifies login, probes a few samples, measures real throughput, then reports scope, cost and ETA.
2. **Manifest** — captures the full work list, reconciles the count against the page, computes the delta against previous runs, and allocates model quota.
3. **Transcription** — downloads media, extracts audio, calls the recognition API, and writes per-item text plus a batch report.

Each round ends with one table and one short report. Nothing else is required from the operator.

## Install

Copy the skill folder into your skills directory:

```
~/.codebuddy/skills/douyin-corpus/
```

Or clone this repository and copy the folder:

```bash
git clone https://github.com/WhisperDR/PlatformTextGrab.git
cp -r PlatformTextGrab/douyin-corpus ~/.codebuddy/skills/
```

## Requirements

| Item | Note |
|---|---|
| Python | 3.10 or newer |
| Packages | `requests`, `openpyxl` |
| ffmpeg / ffprobe | must be on `PATH` |
| Browser automation | Playwright or equivalent, with a logged-in session |

## Usage

Ask the agent to use the skill with an account link:

```
Use douyin-corpus to build a corpus for this account: <profile link>
```

The agent then asks for two things only: the account link, and how to supply the recognition API key (paste once, or a file path).

## Structure

```
douyin-corpus/
├─ SKILL.md                     entry point and workflow
├─ references/
│  ├─ quota-and-models.md       quota table, model chain, switching rules
│  ├─ dashscope-asr.md          API flow and error mapping
│  └─ deliverables.md           directory layout and report format
├─ scripts/
│  ├─ probe_env.py              environment check
│  ├─ init_project.py           scaffold project folders and ledger
│  ├─ build_manifest.py         raw capture -> spreadsheet + reconciliation
│  └─ transcribe.py             batch transcription with rotation
└─ assets/
   ├─ capture.js                in-page capture script
   ├─ gate-template.md          gate report template
   └─ status-template.md        status file template
```

## Defaults

| Parameter | Value |
|---|---|
| Budget cap | 5 CNY per run |
| Concurrency | 3 |
| Audio kept | yes |
| Quota reserve | 5% |
| Budget pause | 80% |
| Circuit break | 5 consecutive failures |
| Progress report | every 20 items or 3 minutes |

## Safety notes

- API keys are read at runtime and never written to disk or echoed.
- Original files are never modified; all outputs are new files.
- A run stops before exceeding the budget, and never falls back to premium-tier models unless explicitly requested.

## License

Not specified. Contact the repository owner before redistribution.
