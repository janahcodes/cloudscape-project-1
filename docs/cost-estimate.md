# Cost Estimate at Three Traffic Levels

All prices are **AWS us-east-2 on-demand list prices, USD per month (730 hours)**.

> My account is on AWS's credit-based Free plan ($100 of credits), so I pay nothing out of pocket at the low level. 
> The tables show list price so the comparison holds for any account.

## How CloudDrop creates cost

- An open room page polls `/api/rooms/{code}/files` every 3 seconds (only
  while the tab is visible). Each poll is one S3 `LIST` call.
- Uploaded files go into S3 (inbound data transfer is free).
- Downloads and image previews come out of S3 directly through presigned
  URLs. That is the main data-transfer-out cost.
- Files are deleted after 24 hours by an S3 lifecycle rule, so storage stays
  at about one day's worth of uploads.

## Workload assumptions

| Assumption | Value | Why |
|---|---|---|
| Request mix | 80% list polls, 10% page/QR loads, 5% uploads, 5% downloads or previews | Polling dominates, because every open room page polls every 3 s |
| S3 calls per request | poll → 1 `LIST`; upload → 1 `PUT`; download → 1 `HEAD` + 1 `GET` | Follows from `app/main.py` |
| Sizes | poll response 1 KB, page 10 KB, average file 1 MB | Typical phone photos and documents |
| Data transfer out per 1M requests | ≈ 52 GB | 0.8 GB polls + 1 GB pages + 50 GB downloads |
| Data stored | 1 day of uploads ≈ monthly uploads ÷ 30 | Lifecycle rule deletes after 1 day |

## Unit prices used

| Item | Price |
|---|---|
| EC2 t3.micro / t3.small | $0.0104 / $0.0208 per hour |
| Public IPv4 address | $0.005 per hour |
| EBS gp3 (8 GB root volume) | $0.08 per GB-month |
| S3 Standard storage | $0.023 per GB-month |
| S3 `PUT`/`LIST` | $0.005 per 1,000 |
| S3 `GET`/`HEAD` | $0.0004 per 1,000 |
| S3 lifecycle expiration | free |
| Data transfer out to internet | first 100 GB/month free, then $0.09/GB |
| Route 53 hosted zone | $0.50 per month |
| Route 53 queries | $0.40 per million (alias queries to AWS resources are free) |
| Application Load Balancer | $0.0225 per hour + $0.008 per LCU-hour |
| CloudFront (level 3 option) | 1 TB + 10M requests free per month, then $0.085/GB |
| Let's Encrypt / ACM certificate | free |
| Domain name | $1.54 for one year then 26.26/year 
---

## Level 1: Low traffic, 10,000 requests/month

A class demo or a few people moving files now and then. About 7 hours of open
room pages per month. 

| Component | Calculation | $/month |
|---|---|---|
| EC2 t3.micro | 730 h × $0.0104 | 7.59 |
| Public IPv4 | 730 h × $0.005 | 3.65 |
| EBS 8 GB gp3 | 8 × $0.08 | 0.64 |
| Route 53 | zone $0.50 + 10k queries | 0.50 |
| S3 requests | 8k LIST ($0.04) + 500 PUT + 1k HEAD/GET | 0.05 |
| S3 storage | ~0.02 GB average | 0.00 |
| Data transfer out | 0.5 GB (inside the 100 GB free allowance) | 0.00 |
| **Total** | | **≈ $12.43** |

Compute plus the public IP are 90% of the bill. Request-driven costs are close to zero.

## Level 2: Medium traffic, 1,000,000 requests/month

A small group using it daily: about 0.4 requests/second on average, and
roughly 670 hours of open room pages per month.
**Architecture: unchanged, but the instance goes up to t3.small** (2 GB RAM) for headroom.

| Component | Calculation | $/month |
|---|---|---|
| EC2 t3.small | 730 h × $0.0208 | 15.18 |
| Public IPv4 | 730 h × $0.005 | 3.65 |
| EBS 8 GB gp3 | | 0.64 |
| Route 53 | zone $0.50 + 1M queries × $0.40/M | 0.90 |
| S3 requests | 800k LIST ($4.00) + 50k PUT ($0.25) + 100k HEAD/GET ($0.04) | 4.29 |
| S3 storage | 50 GB uploaded/month ÷ 30 ≈ 1.7 GB | 0.04 |
| Data transfer out | 52 GB (still inside the free 100 GB) | 0.00 |
| **Total** | | **≈ $24.70** |

**Observation.** S3 `LIST` calls from polling are now the largest variable
cost, about $4. A LIST costs 12.5× as much as a GET.

## Level 3: High traffic, 50,000,000 requests/month

About 19 requests/second on average and 200/s at peak. That's 2.5M uploads and
2.5 TB of downloads per month.
Then architecture must change. One instance is a single point of failure, and a
burstable t3 can't sustain the peak. The changes:

- An Application Load Balancer across 2 Availability Zones, with an
  Auto Scaling group (min 2, max 4, t3.small).
- TLS terminates on the ALB with a free ACM certificate. 
- Route 53 uses an alias record to the ALB, so DNS queries are free.

| Component | Calculation | $/month |
|---|---|---|
| EC2 2 × t3.small (baseline) | 2 × 730 × $0.0208 | 30.37 |
| Public IPv4 | 2 instances + 2 ALB nodes = 4 × $3.65 | 14.60 |
| ALB fixed | 730 × $0.0225 | 16.43 |
| ALB LCUs | ~2.6 TB processed (uploads pass through) ≈ 3.6 LCU × 730 × $0.008 | 21.02 |
| EBS 2 × 8 GB | | 1.28 |
| Route 53 | zone; alias queries free | 0.50 |
| S3 LIST (polling) | 40M × $0.005/1k | 200.00 |
| S3 PUT + HEAD/GET | 2.5M PUT ($12.50) + 5M HEAD/GET ($2.00) | 14.50 |
| S3 storage | 2.5 TB ÷ 30 ≈ 83 GB average | 1.92 |
| Data transfer out | 2,590 GB − 100 GB free = 2,490 × $0.09 | 224.10 |
| **Total (naive scale-out)** | | **≈ $525** |

### Where the money goes and how to cut it

At this level, data transfer out (43%) and S3 LIST polling (38%) are
most of the bill, not the servers.

1. Poll every 10 seconds instead of every 3. That's 70% fewer LIST calls
   (40M → 12M), so $200 drops to $60. Which saves ≈ $140/month. A better long-term
   fix is to push updates to the browser (for example WebSockets) instead of polling.
2. Serve downloads through CloudFront instead of S3 presigned URLs. Transfer
   from S3 to CloudFront is free, and CloudFront includes 1 TB per month free.
   2.5 TB costs (2.5 − 1) TB × $0.085 = $127.50 instead of $224.10.
   Saves ≈ $97/month. This needs CloudFront signed URLs, so it's a real
   design change.

**Optimised total ≈ $288/month.**

## Summary

| Level | Requests/month | Architecture | List price/month | Cost per million requests |
|---|---|---|---|---|
| Low | 10k | 1 × t3.micro + Caddy | ≈ $12.43 | $1,243 |
| Medium | 1M | 1 × t3.small + Caddy | ≈ $24.70 | $24.70 |
| High (naive) | 50M | ALB + ASG (2 × t3.small) | ≈ $525 | $10.50 |
| High (optimised) | 50M | + slower polling + CloudFront | ≈ $288 | $5.76 |

The cost per request falls by over 200× as traffic grows. That is the
economics of elasticity: at low traffic you mostly pay for a server sitting
idle, and at high traffic the cost follows usage. At that point what matters
is how much work each request causes (S3 calls and data transfer). The
biggest single lesson is that a design choice that looks free, polling every
3 seconds, becomes the second-largest line item at scale.
