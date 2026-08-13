# Sandbox Cost per HA

_Last verified: August 12, 2026 · AWS `us-east-1`_

## Estimate

**$24.74 per HA per month** — gross AWS cost; excludes shared environment infrastructure, credits, tax, AWS Support, and model/API usage.

1. **Compute: $10.75** — one On-Demand `r8g.large` at $0.11782/hour for 730 hours, divided across eight configured 256 CPU / 1,920 MiB HA task slots.
2. **Container Insights: $10.99** — 157 Enhanced metrics per HA at $0.07 per metric-month.
3. **Block storage: $2.00** — one 200 GB runtime `gp3` volume per eight-HA host.
4. **Secrets: $0.40** — one Secrets Manager secret per HA.
5. **Light usage: $0.60** — 1 GB of EFS storage, about 5.5 GB of NAT-processed traffic, and 0.1 GB of application logs per HA.

## Variable Costs

1. **Traffic** — $0.045 per NAT-processed GB; internet egress is free within the account's first 100 GB/month, then $0.09 per GB.
2. **Storage and logs** — $0.30 per EFS GB-month, $0.10 per ECR GB-month, and $0.50 per CloudWatch Logs ingested GB after the account free tier.
3. **Load and builds** — $0.008 per ALB capacity-unit hour and $0.08976 per running builder hour; the builder is normally stopped.
4. **Models and external APIs** — separate and dependent on provider, model, tokens, and customer activity.

Shared environment costs are excluded because they do not grow per HA: NAT gateway, application load balancer, public IPv4 addresses, builder storage, container images, DNS, and cluster-level metrics.
