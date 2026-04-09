# Tier Comparison

## Overview

| Feature | Technician | Operations | Master Chief |
|---|---|---|---|
| **Agent** | Cortana | MORPHEUS | Master Chief |
| **Target users** | Line/hangar technicians | MCC, dispatch, supervisors | DOM, CEO, quality managers |
| **Language** | Technical (AMM-level) | Operational (IATA-standard) | Executive (KPI-driven) |
| **Fleet KBs** | ✅ All 3 fleet KBs | ❌ | ✅ All 3 fleet KBs |
| **Institutional KB** | ❌ | ✅ MOE/SMS/MEL | ✅ MOE/SMS/MEL |
| **AMM procedures** | ✅ Full detail | ❌ | ✅ Summary only |
| **MEL dispatch** | ❌ (refer to ops) | ✅ Full MEL lookup | ✅ Overview level |
| **TLB entry generation** | ✅ /tsc commands | ❌ | ❌ |
| **Document ingestion via Telegram** | ✅ | ❌ | ✅ |
| **Voice I/O** | ✅ | ✅ | ✅ |
| **Vision (photo analysis)** | ✅ Maintenance photos | ❌ | ✅ |

## Knowledge Base Access

```
Technician:         [B737 Classic] [B737 NG] [B757]
Operations:         [Institutional: MOE/SMS/MEL/QM]
Master Chief:       [B737 Classic] [B737 NG] [B757] [Institutional]
```

## Pricing Model (SaaS)

| Tier | Included KBs | Suggested Use Case |
|---|---|---|
| Technician | 3 fleet KBs | Line station, hangar, base maintenance teams |
| Operations | 1 institutional KB | MCC, flight dispatch, production planning |
| Master Chief | All 4 KBs | DOM office, quality department, executive team |

Each tier is an independent deployment — no shared infrastructure between customers.

## Agent Personas

### Tier 1 — Cortana (Technician)
Speaks as a senior B1/B2 aircraft maintenance engineer. Delivers exact AMM references, task numbers, torque values, and part numbers. Direct and procedural. Supports bilingual pt-BR / en-US per message.

### Tier 2 — MORPHEUS (Operations)
Speaks as an experienced MCC Duty Manager. Delivers operational decisions (Go/No-Go, MEL category, delay code). Does not go into procedure details. Translates technical faults into dispatch impact.

### Tier 3 — Master Chief (Executive)
Speaks as a Chief Technical Officer. Delivers fleet status, compliance posture, and risk summaries in business language. Leads with conclusions. References regulatory obligations (ANAC/EASA). No AMM detail.

## What Each Tier Cannot Do

| Tier | Limitation | Reason |
|---|---|---|
| Technician | No MEL/MOE access | Not relevant to line maintenance work |
| Operations | No AMM procedures | MCC does not perform maintenance |
| Master Chief | No TLB entry generation | Executive layer does not write technical log entries |

All limitations are intentional — each tier is scoped to prevent information overload and to ensure the right person gets the right answer.
