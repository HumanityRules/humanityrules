# Sandbox Cost per HA

_Last verified: August 10, 2026 · AWS `us-east-1`_

## Estimate

**$33.53 per HA per month** — gross AWS cost; excludes credits, tax, AWS Support, and model/API usage.

1. **Compute: $10.75** — one On-Demand `r8g.large` at $0.11782/hour for 730 hours, divided across eight configured 256 CPU / 1,920 MiB HA task slots.
2. **Container Insights: $11.19** — 157 Enhanced metrics per HA plus one-eighth of 23 cluster metrics, at $0.07 per metric-month.
3. **Fixed networking: $7.53** — one NAT gateway, one application load balancer, and three public IPv4 addresses, divided across eight HAs.
4. **Block storage: $2.50** — 200 GB runtime and 50 GB builder `gp3` volumes, divided across eight HAs.
5. **Container images: $0.50** — the sandbox's current 40.1 GB of ECR storage, divided across eight HAs.
6. **Secrets and DNS: $0.46** — one Secrets Manager secret per HA plus one-eighth of the `humr.io` hosted zone.
7. **Light usage: $0.60** — 1 GB of EFS storage, about 5.5 GB of NAT-processed traffic, and 0.1 GB of application logs per HA.

## Variable Costs

1. **Traffic** — $0.045 per NAT-processed GB; internet egress is free within the account's first 100 GB/month, then $0.09 per GB.
2. **Storage and logs** — $0.30 per EFS GB-month, $0.10 per ECR GB-month, and $0.50 per CloudWatch Logs ingested GB after the account free tier.
3. **Load and builds** — $0.008 per ALB capacity-unit hour and $0.08976 per running builder hour; the builder is normally stopped.
4. **Models and external APIs** — separate and dependent on provider, model, tokens, and customer activity.
