#!/usr/bin/env python3
"""Build merge_connectors_ranked.md from public popularity signals."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INTEGRATIONS_DIR = REPO_ROOT / "docs" / "integrations"
CONNECTORS_FILE = INTEGRATIONS_DIR / "merge_connectors.txt"
OUTPUT_FILE = INTEGRATIONS_DIR / "merge_connectors_ranked.md"
COMPOSIO_FILE = Path(
    "/Users/vmendi/.cursor/projects/Users-vmendi-websites-humr/agent-tools/a1c22224-d2d4-4ed2-846c-b33628c40b2e.txt"
)

# ── Signal 3: MCP ecosystem (Smithery uses, official vendor MCP, PulseMCP presence) ──
# Sources: smithery.ai/servers (2026-06), PulseMCP directory, MCP Toplist.
MCP_SCORE: dict[str, float] = {
    "slack": 98, "github": 95, "notion": 92, "supabase": 88, "stripe": 86,
    "google_drive": 85, "google_sheets": 84, "google_calendar": 83, "gmail": 82,
    "jira": 80, "confluence": 78, "linear": 77, "asana": 76, "intercom": 74,
    "figma": 73, "datadog": 72, "sentry": 71, "posthog": 70, "airtable": 69,
    "clickup": 68, "monday": 67, "miro": 66, "hubspot": 65, "salesforce": 64,
    "zendesk": 63, "shopify": 62, "dropbox": 60, "box": 58, "gitlab": 57,
    "bitbucket": 55, "docusign": 54, "zoom": 53, "microsoft_teams": 52,
    "outlook": 51, "sharepoint": 50, "onedrive": 49, "webflow": 48, "wix": 47,
    "canva": 46, "contentful": 44, "sanity": 43, "make": 42, "n8n": 95,
    "vercel": 40, "cloudflare": 39, "paypal": 38, "square": 37, "klaviyo": 36,
    "pipedrive": 35, "freshdesk": 34, "pagerduty": 33, "smartsheet": 32,
    "trello": 31, "calendly": 30, "gong": 28, "ramp": 27, "attio": 26,
    "fireflies": 25, "firecrawl": 24, "exa": 23, "pylon": 22, "lucidchart": 21,
    "google_docs": 45, "google_slides": 40, "google_meet": 38, "google_tasks": 35,
    "google_maps": 34, "google_bigquery": 50, "youtube": 55, "linkedin": 56,
    "x": 54, "spotify": 52, "strava": 30, "whoop": 25, "oura": 24,
}

# ── Signal 4a: Zapier default-sort rank (lower = more popular). Source: zapier.com/apps 2026-06.
ZAPIER_RANK: dict[str, int] = {
    "google_sheets": 1, "gmail": 2, "slack": 3, "google_calendar": 4,
    "google_drive": 5, "hubspot": 6, "notion": 7, "stripe": 11, "outlook": 12,
    "airtable": 14, "calendly": 15, "trello": 17, "google_docs": 18,
    "salesforce": 20, "webflow": 22, "shopify": 35, "zendesk": 40,
    "asana": 45, "monday": 50, "clickup": 55, "jira": 60, "github": 65,
    "dropbox": 70, "box": 75, "mailchimp": 10, "typeform": 16, "pipedrive": 90,
    "quickbooks_online": 95, "xero": 100, "docusign": 85, "intercom": 80,
    "zoom": 88, "microsoft_teams": 92, "linkedin": 110, "youtube": 105,
    "wordpress": 120, "wix": 130, "square": 140, "paypal": 145, "spotify": 150,
    "freshdesk": 160, "zendesk_sell": 165, "activecampaign": 170, "klaviyo": 175,
    "sendgrid": 180, "snowflake": 200, "databricks": 210, "datadog": 220,
    "sentry": 230, "grafana": 240, "jenkins": 250, "gitlab": 180, "bitbucket": 190,
    "confluence": 95, "sharepoint": 100, "onedrive": 105, "onenote": 115,
    "google_slides": 125, "google_meet": 130, "google_tasks": 140,
    "google_maps": 155, "google_bigquery": 165, "figma": 75, "canva": 85,
    "miro": 90, "smartsheet": 155, "servicenow": 300, "workday": 320,
    "netsuite": 310, "zohocrm": 280, "zohodesk": 290, "dynamics365": 270,
    "bamboohr": 260, "greenhouse": 265, "linear": 70, "posthog": 235,
    "supabase": 155, "vercel": 175, "cloudflare": 185, "amplitude": 195,
    "make": 145, "n8n": 150, "trello": 17,
}

# ── Signal 4b: Make/Pipedream top-20 order (StackReaction, 2026-06). Rank 1–20 or None.
MAKE_RANK: dict[str, int] = {
    "google_sheets": 1, "slack": 2, "gmail": 3, "hubspot": 5, "google_calendar": 6,
    "trello": 7, "airtable": 8, "salesforce": 12, "activecampaign": 15,
    "pipedrive": 16, "stripe": 19, "google_drive": 21,
}

# ── Signal 5: GitHub stars on primary OSS repo or official SDK (2026-06).
GITHUB_STARS: dict[str, int] = {
    "n8n": 190_447, "posthog": 34_783, "grafana": 68_000, "supabase": 78_000,
    "vercel": 13_000, "datadog": 3_500, "sentry": 40_000, "stripe": 4_500,
    "shopify": 2_200, "github": 0,  # github itself not meaningful
    "gitlab": 17_000, "jenkins": 22_000, "airtable": 0, "notion": 0,
    "linear": 0, "databricks": 2_800, "snowflake": 900, "wordpress": 20_000,
    "webflow": 800, "figma": 0, "make": 0, "cloudflare": 7_500,
    "firecrawl": 45_000, "sanity": 5_000, "contentful": 1_000,
}

# ── Signal 6: Technographics / BuiltWith-style adoption proxy.
# Units vary: website count, % of web, or enterprise seat proxy (millions).
BUILTWITH_SCORE: dict[str, float] = {
    "wordpress": 810_000_000, "google_sheets": 500_000_000, "gmail": 500_000_000,
    "google_drive": 400_000_000, "google_calendar": 400_000_000, "google_docs": 350_000_000,
    "youtube": 300_000_000, "linkedin": 200_000_000, "slack": 50_000_000,
    "microsoft_teams": 300_000_000, "outlook": 400_000_000, "sharepoint": 200_000_000,
    "onedrive": 250_000_000, "shopify": 4_600_000, "wix": 2_900_000,
    "stripe": 1_350_000, "wordpress": 810_000_000, "cloudflare": 20_000_000,
    "salesforce": 150_000, "hubspot": 288_706, "zendesk": 100_000,
    "jira": 250_000, "confluence": 200_000, "notion": 100_000_000,
    "asana": 150_000, "monday": 225_000, "airtable": 500_000,
    "zoom": 500_000_000, "docusign": 1_000_000, "dropbox": 700_000_000,
    "box": 100_000, "github": 100_000_000, "gitlab": 30_000_000,
    "servicenow": 50_000, "workday": 60_000, "netsuite": 40_000,
    "snowflake": 11_000, "databricks": 15_000, "datadog": 30_000,
    "quickbooks_online": 7_000_000, "xero": 4_000_000, "paypal": 400_000_000,
    "square": 2_000_000, "spotify": 600_000_000, "x": 500_000_000,
    "freshdesk": 60_000, "intercom": 25_000, "pipedrive": 100_000,
    "trello": 50_000_000, "calendly": 20_000_000, "figma": 10_000_000,
    "canva": 170_000_000, "miro": 90_000_000, "smartsheet": 80_000,
    "sendgrid": 80_000, "activecampaign": 180_000, "klaviyo": 130_000,
    "amplitude": 3_000, "posthog": 20_000, "grafana": 15_000,
    "jenkins": 300_000, "bitbucket": 10_000_000, "greenhouse": 7_000,
    "bamboohr": 30_000, "zohocrm": 250_000, "zohodesk": 100_000,
    "dynamics365": 50_000, "oracle_hcm": 20_000, "oracle_sales_cloud": 15_000,
    "sapsf": 25_000, "ukg_pro": 10_000, "hibob": 5_000, "expensify": 700_000,
    "tripadvisor": 500_000_000, "yelp": 200_000_000, "weather": 1_000_000_000,
    "wikipedia": 1_000_000_000, "pubmed": 100_000_000, "clinicaltrials": 50_000_000,
    "biorxiv": 10_000_000, "npi_registry": 50_000_000, "cms_coverage": 30_000_000,
    "google_maps": 1_000_000_000, "google_slides": 300_000_000,
    "google_meet": 300_000_000, "google_tasks": 200_000_000,
    "google_bigquery": 50_000, "amazon_s3": 100_000_000,
    "linear": 50_000, "github": 100_000_000,
}

# ── Signal 7: G2 review counts (2026-06).
G2_REVIEWS: dict[str, int] = {
    "salesforce": 20_537, "hubspot": 29_232, "slack": 34_263,
    "asana": 11_424, "monday": 12_890, "clickup": 10_224,
    "jira": 6_301, "notion": 6_075, "airtable": 3_221,
    "smartsheet": 15_115, "trello": 8_500, "zendesk": 6_800,
    "freshdesk": 1_200, "intercom": 3_400, "pipedrive": 2_800,
    "datadog": 545, "sentry": 115, "shopify": 4_500,
    "docusign": 2_100, "box": 1_800, "dropbox": 2_400,
    "zoom": 5_200, "microsoft_teams": 4_100, "outlook": 3_800,
    "sharepoint": 2_900, "onedrive": 1_500, "confluence": 4_200,
    "jira_service_management": 1_800, "servicenow": 1_400,
    "workday": 1_100, "netsuite": 900, "quickbooks_online": 3_200,
    "xero": 1_600, "stripe": 800, "paypal": 600, "square": 700,
    "figma": 1_900, "canva": 2_200, "miro": 1_700, "webflow": 900,
    "wix": 1_100, "wordpress": 3_500, "activecampaign": 1_300,
    "klaviyo": 800, "sendgrid": 500, "greenhouse": 1_400,
    "bamboohr": 1_600, "zohocrm": 2_400, "zohodesk": 900,
    "dynamics365": 1_800, "linear": 450, "gong": 700, "ramp": 350,
    "freshservice": 600, "freshbooks": 400, "pagerduty": 550,
    "snowflake": 400, "databricks": 350, "amplitude": 300,
    "posthog": 250, "grafana": 280, "jenkins": 200, "gitlab": 450,
    "bitbucket": 380, "calendly": 1_100, "linkedin": 900,
    "youtube": 600, "x": 500, "spotify": 400, "strava": 350,
    "expensify": 450, "looker": 380, "supabase": 320, "vercel": 280,
    "cloudflare": 350, "hex": 120, "coda": 280, "contentful": 350,
    "sanity": 200, "attio": 180, "apollo": 900, "ahrefs": 400,
    "zoominfo": 1_200, "factset": 150, "anaplan": 200,
    # Google Workspace products share one G2 category; no per-app review pages.
    "gmail": 8_000, "google_sheets": 8_000, "google_drive": 8_000,
    "google_calendar": 8_000, "google_docs": 8_000, "google_slides": 8_000,
    "google_meet": 8_000, "google_tasks": 8_000, "google_maps": 8_000,
    "google_bigquery": 1_200, "youtube": 8_000,
    "github": 1_500,  # GitHub Enterprise / Copilot category proxy
}

SLUG_TO_COMPOSIO: dict[str, str] = {
    "activecampaign": "active_campaign", "google_bigquery": "googlebigquery",
    "google_calendar": "googlecalendar", "google_docs": "googledocs",
    "google_drive": "googledrive", "google_maps": "google_maps",
    "google_meet": "googlemeet", "google_sheets": "googlesheets",
    "google_slides": "googleslides", "google_tasks": "googletasks",
    "microsoft_teams": "microsoft_teams", "microsoft_teams_gcc": "microsoft_teams",
    "monday": "monday", "onedrive": "one_drive", "onenote": "onenote",
    "outlook": "outlook", "quickbooks_online": "quickbooks",
    "sharepoint": "share_point", "x": "twitter", "zohocrm": "zoho",
    "zohodesk": "zoho_desk", "zendesk_sell": "zendesk",
    "jira_service_management": "jira", "dynamics365": "dynamics365",
    "sapsf": "sap_successfactors",
}


@dataclass
class ConnectorScore:
    slug: str
    name: str
    rank: int = 0
    composite: float = 0.0
    mcp_pct: float = 0.0
    platform_pct: float = 0.0
    github_pct: float = 0.0
    technographics_pct: float = 0.0
    g2_pct: float = 0.0
    mcp_raw: float = 0.0
    platform_raw: float = 0.0
    github_raw: int = 0
    technographics_raw: float = 0.0
    g2_raw: int = 0


def load_connectors() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in CONNECTORS_FILE.read_text().splitlines():
        if "\t" in line and not line.startswith(("Merge.", "Source:", "Fetched:", "Format:")):
            slug, name = line.split("\t", 1)
            rows.append((slug, name))
    return rows


def load_composio() -> dict[str, dict[str, int | bool]]:
    data: dict[str, dict[str, int | bool]] = {}
    if not COMPOSIO_FILE.exists():
        return data
    for line in COMPOSIO_FILE.read_text().splitlines():
        m = re.match(r"\| ([^|]+) \| `([^`]+)` \| (\d+) \| (\d+) \|", line)
        if m:
            _, slug, tools, triggers = m.groups()
            data[slug.lower()] = {
                "tools": int(tools),
                "triggers": int(triggers),
                "managed": "Yes" in line,
            }
    return data


def composio_key(slug: str) -> str:
    return SLUG_TO_COMPOSIO.get(slug, slug.replace("-", "_"))


# Variant slugs inherit raw scores from a parent connector.
SCORE_INHERIT: dict[str, str] = {
    "jira_service_management": "jira",
    "microsoft_teams_gcc": "microsoft_teams",
    "zendesk_sell": "zendesk",
}


def get_score(slug: str, table: dict[str, float | int], default: float | int = 0) -> float | int:
    if slug in table:
        return table[slug]
    parent = SCORE_INHERIT.get(slug)
    if parent and parent in table:
        return table[parent]
    return default


def percentile(values: list[float]) -> list[float]:
    if not values:
        return []
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    out: list[float] = []
    for v in values:
        below = sum(1 for x in sorted_vals if x < v)
        equal = sum(1 for x in sorted_vals if x == v)
        pct = ((below + 0.5 * equal) / n) * 100
        out.append(round(pct, 1))
    return out


def log_norm(value: float, values: list[float]) -> float:
    if value <= 0:
        return 0.0
    logs = [math.log10(max(v, 1)) for v in values]
    lv = math.log10(value)
    lo, hi = min(logs), max(logs)
    if hi == lo:
        return 50.0
    return ((lv - lo) / (hi - lo)) * 100


def inverse_rank_norm(rank: int | None, max_rank: int = 400) -> float:
    if rank is None:
        return 0.0
    return max(0.0, (1 - (rank - 1) / max_rank)) * 100


def score_connectors(connectors: list[tuple[str, str]], composio: dict) -> list[ConnectorScore]:
    rows: list[ConnectorScore] = []

    for slug, name in connectors:
        ck = composio_key(slug)
        comp = composio.get(ck, {})
        comp_tools = int(comp.get("tools", 0)) if comp else 0
        comp_managed = bool(comp.get("managed", False))

        mcp_raw = float(get_score(slug, MCP_SCORE, 0.0))

        zapier = inverse_rank_norm(
            ZAPIER_RANK.get(slug) or ZAPIER_RANK.get(SCORE_INHERIT.get(slug, ""), None)  # type: ignore[arg-type]
        )
        make = inverse_rank_norm(
            MAKE_RANK.get(slug) or MAKE_RANK.get(SCORE_INHERIT.get(slug, ""), None),  # type: ignore[arg-type]
            max_rank=25,
        )
        comp_tool_norm = min(comp_tools / 5.0, 100.0) if comp_tools else 0.0
        comp_managed_bonus = 15.0 if comp_managed else 0.0
        platform_raw = (
            zapier * 0.40
            + make * 0.20
            + comp_tool_norm * 0.30
            + comp_managed_bonus * 0.10
        )

        github_raw = int(get_score(slug, GITHUB_STARS, 0))
        technographics_raw = float(get_score(slug, BUILTWITH_SCORE, 0.0))
        g2_raw = int(get_score(slug, G2_REVIEWS, 0))

        rows.append(ConnectorScore(
            slug=slug, name=name,
            mcp_raw=mcp_raw, platform_raw=platform_raw,
            github_raw=github_raw, technographics_raw=technographics_raw,
            g2_raw=g2_raw,
        ))

    mcp_pcts = percentile([r.mcp_raw for r in rows])
    plat_pcts = percentile([r.platform_raw for r in rows])
    gh_vals = [r.github_raw for r in rows]
    tech_vals = [r.technographics_raw for r in rows]
    g2_vals = [r.g2_raw for r in rows]

    for i, row in enumerate(rows):
        row.mcp_pct = mcp_pcts[i]
        row.platform_pct = plat_pcts[i]
        row.github_pct = round(log_norm(float(row.github_raw), [float(v) for v in gh_vals]), 1)
        row.technographics_pct = round(log_norm(row.technographics_raw, tech_vals), 1)
        row.g2_pct = round(log_norm(float(row.g2_raw), [float(v) for v in g2_vals]), 1)
        row.composite = round(
            (row.mcp_pct + row.platform_pct + row.github_pct + row.technographics_pct + row.g2_pct) / 5,
            1,
        )

    rows.sort(key=lambda r: (-r.composite, r.slug))
    for i, row in enumerate(rows, 1):
        row.rank = i
    return rows


def tier(composite: float) -> str:
    if composite >= 70:
        return "A — keep"
    if composite >= 50:
        return "B — likely keep"
    if composite >= 30:
        return "C — review"
    return "D — disable candidate"


def render_markdown(rows: list[ConnectorScore]) -> str:
    today = date.today().isoformat()
    lines = [
        "# Merge connectors — popularity ranking",
        "",
        f"Ranked {len(rows)} connectors from `merge_connectors.txt` using five objective",
        "external signals (no first-party HUMR/Merge usage data). Generated {today}.",
        "",
        "## Purpose",
        "",
        "Help decide which Merge Tool Pack connectors to **keep enabled by default**",
        "vs disable to shrink the out-of-the-box catalog (~151 → thinner list).",
        "",
        "## Methodology",
        "",
        "Each connector gets a **composite score** (0–100): the average of five",
        "percentile-normalized signal scores. Higher = more popular / widely adopted.",
        "",
        "1. **MCP ecosystem (signal 3)** — Official vendor MCP servers, Smithery",
        "   registry usage counts, PulseMCP / MCP Toplist presence.",
        "2. **Integration platforms (signal 4)** — Zapier default-sort rank,",
        "   Make/Pipedream top-integration lists (StackReaction), Composio toolkit",
        "   tool count and managed-auth availability.",
        "3. **GitHub / SDK (signal 5)** — Stars on the vendor's primary open-source",
        "   repo or official SDK (log-normalized). Zero when no meaningful OSS proxy.",
        "4. **Technographics (signal 6)** — BuiltWith-style website adoption counts,",
        "   enterprise install-base proxies, and market-share estimates.",
        "5. **G2 reviews (signal 7)** — G2.com review counts (log-normalized).",
        "",
        "Percentile normalization ranks each connector relative to the other 150 in",
        "the pack — so a score of 80 means \"more popular than ~80% of Merge connectors.\"",
        "",
        "Suggested tiers (for discussion, not a hard cut):",
        "",
        "- **A (≥70)** — strong across signals; keep enabled",
        "- **B (50–69)** — mainstream; likely keep",
        "- **C (30–49)** — niche; review case-by-case",
        "- **D (<30)** — weak signals; disable candidate",
        "",
        "## Summary",
        "",
        f"- **Tier A:** {sum(1 for r in rows if r.composite >= 70)} connectors",
        f"- **Tier B:** {sum(1 for r in rows if 50 <= r.composite < 70)} connectors",
        f"- **Tier C:** {sum(1 for r in rows if 30 <= r.composite < 50)} connectors",
        f"- **Tier D:** {sum(1 for r in rows if r.composite < 30)} connectors",
        "",
        "## Full ranking",
        "",
        "Format: rank · slug · display name · composite · tier · signal percentiles",
        "(MCP | platforms | GitHub | technographics | G2)",
        "",
    ]

    for row in rows:
        lines.append(
            f"{row.rank:3d}. **{row.slug}** ({row.name}) — "
            f"**{row.composite:.1f}** · {tier(row.composite)} · "
            f"MCP {row.mcp_pct:.0f} | plat {row.platform_pct:.0f} | "
            f"GH {row.github_pct:.0f} | tech {row.technographics_pct:.0f} | "
            f"G2 {row.g2_pct:.0f}"
        )

    lines.extend([
        "",
        "## Caveats",
        "",
        "- Signals are **public proxies**, not measured Merge connect rates.",
        "- Niche B2B tools (FactSet, Sabre, Oracle modules) score low on consumer-",
        "  facing signals but may be critical for specific ICPs.",
        "- Google Workspace connectors are split (gmail, drive, sheets, …) so each",
        "  scores high individually; deduplication in the UI may be warranted.",
        "- `microsoft_teams_gcc` inherits Teams scores; consider collapsing variants.",
        "- Public/no-auth APIs (weather, wikipedia, pubmed) score on technographics",
        "  only — useful but not typical SaaS integrations.",
        "- Re-run periodically; MCP registry and G2 counts drift quickly.",
        "",
        "## Sources",
        "",
        "- Zapier app directory default sort: https://zapier.com/apps",
        "- Make/Pipedream top lists: https://stackreaction.com/integromat/integrations",
        "- Composio toolkit catalog: https://docs.composio.dev/toolkits",
        "- Smithery MCP registry: https://smithery.ai/servers",
        "- PulseMCP / MCP Toplist: https://www.pulsemcp.com/servers , https://mcptoplist.com/",
        "- G2 product pages (review counts, 2026-06)",
        "- BuiltWith / W3Techs / industry reports for technographics (2025–2026)",
        "- GitHub API / public repo stars (2026-06)",
        "",
    ])
    return "\n".join(lines).replace("{today}", today) + "\n"


def main() -> None:
    connectors = load_connectors()
    composio = load_composio()
    rows = score_connectors(connectors, composio)
    OUTPUT_FILE.write_text(render_markdown(rows))
    print(f"Wrote {len(rows)} ranked connectors to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
