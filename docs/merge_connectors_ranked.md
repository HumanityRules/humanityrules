# Merge connectors — popularity ranking

Ranked 151 connectors from `merge_connectors.txt` using five objective
external signals (no first-party DOH/Merge usage data). Generated 2026-06-08.

## Purpose

Help decide which Merge Tool Pack connectors to **keep enabled by default**
vs disable to shrink the out-of-the-box catalog (~151 → thinner list).

## Methodology

Each connector gets a **composite score** (0–100): the average of five
percentile-normalized signal scores. Higher = more popular / widely adopted.

1. **MCP ecosystem (signal 3)** — Official vendor MCP servers, Smithery
   registry usage counts, PulseMCP / MCP Toplist presence.
2. **Integration platforms (signal 4)** — Zapier default-sort rank,
   Make/Pipedream top-integration lists (StackReaction), Composio toolkit
   tool count and managed-auth availability.
3. **GitHub / SDK (signal 5)** — Stars on the vendor's primary open-source
   repo or official SDK (log-normalized). Zero when no meaningful OSS proxy.
4. **Technographics (signal 6)** — BuiltWith-style website adoption counts,
   enterprise install-base proxies, and market-share estimates.
5. **G2 reviews (signal 7)** — G2.com review counts (log-normalized).

Percentile normalization ranks each connector relative to the other 150 in
the pack — so a score of 80 means "more popular than ~80% of Merge connectors."

Suggested tiers (for discussion, not a hard cut):

- **A (≥70)** — strong across signals; keep enabled
- **B (50–69)** — mainstream; likely keep
- **C (30–49)** — niche; review case-by-case
- **D (<30)** — weak signals; disable candidate

## Summary

- **Tier A:** 11 connectors
- **Tier B:** 57 connectors
- **Tier C:** 22 connectors
- **Tier D:** 61 connectors

## Full ranking

Format: rank · slug · display name · composite · tier · signal percentiles
(MCP | platforms | GitHub | technographics | G2)

  1. **stripe** (Stripe) — **79.2** · A — keep · MCP 96 | plat 98 | GH 69 | tech 68 | G2 64
  2. **shopify** (Shopify) — **78.4** · A — keep · MCP 81 | plat 93 | GH 63 | tech 74 | G2 81
  3. **slack** (Slack) — **76.6** · A — keep · MCP 100 | plat 98 | GH 0 | tech 86 | G2 100
  4. **google_sheets** (Google Sheets) — **74.7** · A — keep · MCP 95 | plat 96 | GH 0 | tech 97 | G2 86
  5. **gmail** (Gmail) — **74.2** · A — keep · MCP 94 | plat 94 | GH 0 | tech 97 | G2 86
  6. **google_calendar** (Google Calendar) — **73.7** · A — keep · MCP 94 | plat 92 | GH 0 | tech 96 | G2 86
  7. **google_drive** (Google Drive) — **73.2** · A — keep · MCP 96 | plat 88 | GH 0 | tech 96 | G2 86
  8. **posthog** (PostHog) — **72.2** · A — keep · MCP 87 | plat 87 | GH 86 | tech 48 | G2 53
  9. **gitlab** (GitLab) — **71.6** · A — keep · MCP 79 | plat 57 | GH 80 | tech 83 | G2 58
 10. **github** (GitHub) — **70.9** · A — keep · MCP 99 | plat 97 | GH 0 | tech 89 | G2 70
 11. **notion** (Notion) — **70.5** · A — keep · MCP 98 | plat 82 | GH 0 | tech 89 | G2 83
 12. **wordpress** (WordPress) — **68.6** · B — likely keep · MCP 24 | plat 60 | GH 82 | tech 99 | G2 78
 13. **hubspot** (HubSpot) — **68.4** · B — likely keep · MCP 84 | plat 99 | GH 0 | tech 61 | G2 98
 14. **dropbox** (Dropbox) — **67.7** · B — likely keep · MCP 80 | plat 85 | GH 0 | tech 98 | G2 74
 15. **outlook** (Outlook) — **67.7** · B — likely keep · MCP 72 | plat 92 | GH 0 | tech 96 | G2 79
 16. **salesforce** (Salesforce) — **66.1** · B — likely keep · MCP 83 | plat 95 | GH 0 | tech 58 | G2 95
 17. **zoom** (Zoom) — **66.1** · B — likely keep · MCP 75 | plat 76 | GH 0 | tech 97 | G2 82
 18. **cloudflare** (Cloudflare) — **65.7** · B — likely keep · MCP 64 | plat 54 | GH 73 | tech 81 | G2 56
 19. **google_docs** (Google Docs) — **65.7** · B — likely keep · MCP 68 | plat 80 | GH 0 | tech 95 | G2 86
 20. **microsoft_teams** (Microsoft Teams) — **65.7** · B — likely keep · MCP 74 | plat 81 | GH 0 | tech 94 | G2 80
 21. **microsoft_teams_gcc** (Microsoft Teams GCC High) — **65.7** · B — likely keep · MCP 74 | plat 81 | GH 0 | tech 94 | G2 80
 22. **trello** (Trello) — **65.7** · B — likely keep · MCP 57 | plat 100 | GH 0 | tech 86 | G2 87
 23. **youtube** (YouTube) — **65.4** · B — likely keep · MCP 78 | plat 69 | GH 0 | tech 94 | G2 86
 24. **asana** (Asana) — **64.8** · B — likely keep · MCP 90 | plat 86 | GH 0 | tech 58 | G2 90
 25. **monday** (Monday.com) — **63.8** · B — likely keep · MCP 85 | plat 84 | GH 0 | tech 60 | G2 91
 26. **miro** (Miro) — **63.7** · B — likely keep · MCP 84 | plat 74 | GH 0 | tech 88 | G2 71
 27. **zendesk** (Zendesk) — **63.7** · B — likely keep · MCP 82 | plat 96 | GH 0 | tech 56 | G2 84
 28. **airtable** (Airtable) — **63.6** · B — likely keep · MCP 86 | plat 91 | GH 0 | tech 63 | G2 77
 29. **datadog** (Datadog) — **63.5** · B — likely keep · MCP 88 | plat 52 | GH 67 | tech 50 | G2 60
 30. **figma** (Figma) — **62.9** · B — likely keep · MCP 89 | plat 75 | GH 0 | tech 78 | G2 72
 31. **jira** (Jira) — **62.9** · B — likely keep · MCP 93 | plat 78 | GH 0 | tech 60 | G2 84
 32. **sharepoint** (SharePoint) — **62.8** · B — likely keep · MCP 72 | plat 74 | GH 0 | tech 92 | G2 76
 33. **supabase** (Supabase) — **62.6** · B — likely keep · MCP 97 | plat 68 | GH 93 | tech 0 | G2 55
 34. **zendesk_sell** (Zendesk Sell) — **62.5** · B — likely keep · MCP 82 | plat 90 | GH 0 | tech 56 | G2 84
 35. **google_slides** (Google Slides) — **61.7** · B — likely keep · MCP 65 | plat 63 | GH 0 | tech 94 | G2 86
 36. **docusign** (DocuSign) — **61.2** · B — likely keep · MCP 76 | plat 90 | GH 0 | tech 67 | G2 73
 37. **google_meet** (Google Meet) — **61.2** · B — likely keep · MCP 63 | plat 63 | GH 0 | tech 94 | G2 86
 38. **canva** (Canva) — **61.1** · B — likely keep · MCP 68 | plat 72 | GH 0 | tech 91 | G2 74
 39. **google_maps** (Google Maps) — **60.9** · B — likely keep · MCP 59 | plat 59 | GH 0 | tech 100 | G2 86
 40. **onedrive** (OneDrive) — **60.7** · B — likely keep · MCP 70 | plat 70 | GH 0 | tech 93 | G2 70
 41. **jira_service_management** (Jira Service Management) — **60.5** · B — likely keep · MCP 93 | plat 78 | GH 0 | tech 60 | G2 72
 42. **confluence** (Confluence) — **60.3** · B — likely keep · MCP 92 | plat 71 | GH 0 | tech 59 | G2 80
 43. **linkedin** (LinkedIn) — **60.3** · B — likely keep · MCP 78 | plat 66 | GH 0 | tech 92 | G2 65
 44. **google_tasks** (Google Tasks) — **59.8** · B — likely keep · MCP 60 | plat 61 | GH 0 | tech 92 | G2 86
 45. **box** (Box) — **59.3** · B — likely keep · MCP 80 | plat 89 | GH 0 | tech 56 | G2 72
 46. **intercom** (Intercom) — **59.1** · B — likely keep · MCP 90 | plat 79 | GH 0 | tech 49 | G2 78
 47. **spotify** (Spotify) — **58.7** · B — likely keep · MCP 74 | plat 65 | GH 0 | tech 98 | G2 57
 48. **calendly** (Calendly) — **57.1** · B — likely keep · MCP 56 | plat 82 | GH 0 | tech 81 | G2 67
 49. **pipedrive** (Pipedrive) — **57.1** · B — likely keep · MCP 60 | plat 94 | GH 0 | tech 56 | G2 76
 50. **sentry** (Sentry) — **57.1** · B — likely keep · MCP 88 | plat 65 | GH 87 | tech 0 | G2 45
 51. **paypal** (PayPal) — **56.7** · B — likely keep · MCP 63 | plat 64 | GH 0 | tech 96 | G2 61
 52. **databricks** (Databricks) — **55.3** · B — likely keep · MCP 24 | plat 84 | GH 65 | tech 46 | G2 56
 53. **linear** (Linear) — **55.0** · B — likely keep · MCP 91 | plat 73 | GH 0 | tech 52 | G2 58
 54. **bitbucket** (Bitbucket) — **54.8** · B — likely keep · MCP 78 | plat 62 | GH 0 | tech 78 | G2 57
 55. **x** (X) — **54.6** · B — likely keep · MCP 76 | plat 41 | GH 0 | tech 97 | G2 60
 56. **grafana** (Grafana) — **53.4** · B — likely keep · MCP 24 | plat 51 | GH 92 | tech 46 | G2 54
 57. **webflow** (Webflow) — **53.4** · B — likely keep · MCP 70 | plat 77 | GH 55 | tech 0 | G2 65
 58. **jenkins** (Jenkins) — **53.3** · B — likely keep · MCP 24 | plat 49 | GH 82 | tech 61 | G2 51
 59. **wix** (Wix) — **53.3** · B — likely keep · MCP 69 | plat 58 | GH 0 | tech 72 | G2 67
 60. **square** (Square) — **52.6** · B — likely keep · MCP 62 | plat 68 | GH 0 | tech 70 | G2 63
 61. **clickup** (ClickUp) — **52.0** · B — likely keep · MCP 86 | plat 86 | GH 0 | tech 0 | G2 88
 62. **smartsheet** (Smartsheet) — **52.0** · B — likely keep · MCP 57 | plat 56 | GH 0 | tech 54 | G2 92
 63. **n8n** (n8n) — **51.1** · B — likely keep · MCP 99 | plat 57 | GH 100 | tech 0 | G2 0
 64. **klaviyo** (Klaviyo) — **50.9** · B — likely keep · MCP 61 | plat 72 | GH 0 | tech 57 | G2 64
 65. **quickbooks_online** (QuickBooks Online) — **50.7** · B — likely keep · MCP 24 | plat 76 | GH 0 | tech 76 | G2 77
 66. **google_bigquery** (Google BigQuery) — **50.6** · B — likely keep · MCP 72 | plat 61 | GH 0 | tech 52 | G2 68
 67. **freshdesk** (Freshdesk) — **50.1** · B — likely keep · MCP 59 | plat 70 | GH 0 | tech 53 | G2 68
 68. **vercel** (Vercel) — **50.0** · B — likely keep · MCP 65 | plat 53 | GH 78 | tech 0 | G2 54
 69. **activecampaign** (ActiveCampaign) — **47.8** · C — review · MCP 24 | plat 88 | GH 0 | tech 58 | G2 69
 70. **xero** (Xero) — **47.1** · C — review · MCP 24 | plat 67 | GH 0 | tech 73 | G2 71
 71. **snowflake** (Snowflake) — **47.0** · C — review · MCP 24 | plat 53 | GH 56 | tech 45 | G2 57
 72. **contentful** (Contentful) — **44.7** · C — review · MCP 67 | plat 43 | GH 57 | tech 0 | G2 56
 73. **sendgrid** (SendGrid) — **44.3** · C — review · MCP 24 | plat 83 | GH 0 | tech 54 | G2 60
 74. **sanity** (Sanity) — **44.2** · C — review · MCP 67 | plat 34 | GH 70 | tech 0 | G2 51
 75. **zohocrm** (Zoho CRM) — **41.3** · C — review · MCP 24 | plat 48 | GH 0 | tech 60 | G2 74
 76. **dynamics365** (Dynamics 365 Sales) — **39.5** · C — review · MCP 24 | plat 49 | GH 0 | tech 52 | G2 72
 77. **servicenow** (ServiceNow) — **39.4** · C — review · MCP 24 | plat 51 | GH 0 | tech 52 | G2 69
 78. **bamboohr** (BambooHR) — **38.9** · C — review · MCP 24 | plat 50 | GH 0 | tech 50 | G2 71
 79. **zohodesk** (Zoho Desk) — **38.3** · C — review · MCP 24 | plat 47 | GH 0 | tech 56 | G2 65
 80. **workday** (Workday) — **37.8** · C — review · MCP 24 | plat 45 | GH 0 | tech 53 | G2 67
 81. **netsuite** (NetSuite) — **37.6** · C — review · MCP 24 | plat 47 | GH 0 | tech 51 | G2 65
 82. **greenhouse** (Greenhouse) — **36.5** · C — review · MCP 24 | plat 46 | GH 0 | tech 43 | G2 69
 83. **firecrawl** (Firecrawl) — **35.0** · C — review · MCP 51 | plat 36 | GH 88 | tech 0 | G2 0
 84. **pagerduty** (PagerDuty) — **34.6** · C — review · MCP 58 | plat 55 | GH 0 | tech 0 | G2 60
 85. **amplitude** (Amplitude) — **34.5** · C — review · MCP 24 | plat 55 | GH 0 | tech 39 | G2 55
 86. **expensify** (Expensify) — **32.4** · C — review · MCP 24 | plat 15 | GH 0 | tech 65 | G2 58
 87. **gong** (Gong) — **31.7** · C — review · MCP 55 | plat 41 | GH 0 | tech 0 | G2 63
 88. **tripadvisor** (TripAdvisor) — **30.5** · C — review · MCP 24 | plat 32 | GH 0 | tech 97 | G2 0
 89. **ramp** (Ramp) — **30.4** · C — review · MCP 54 | plat 42 | GH 0 | tech 0 | G2 56
 90. **strava** (Strava) — **30.2** · C — review · MCP 56 | plat 39 | GH 0 | tech 0 | G2 56
 91. **attio** (Attio) — **29.4** · D — disable candidate · MCP 53 | plat 44 | GH 0 | tech 0 | G2 50
 92. **weather** (Weather) — **27.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 100 | G2 0
 93. **wikipedia** (Wikipedia) — **27.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 100 | G2 0
 94. **yelp** (Yelp) — **26.2** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 92 | G2 0
 95. **apollo** (Apollo) — **25.6** · D — disable candidate · MCP 24 | plat 39 | GH 0 | tech 0 | G2 65
 96. **amazon_s3** (Amazon S3) — **25.5** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 89 | G2 0
 97. **pubmed** (PubMed) — **25.5** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 89 | G2 0
 98. **zoominfo** (ZoomInfo) — **25.2** · D — disable candidate · MCP 24 | plat 34 | GH 0 | tech 0 | G2 68
 99. **clinicaltrials** (ClinicalTrials.gov) — **24.9** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 86 | G2 0
100. **npi_registry** (NPI Registry) — **24.9** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 86 | G2 0
101. **make** (Make) — **24.8** · D — disable candidate · MCP 66 | plat 58 | GH 0 | tech 0 | G2 0
102. **cms_coverage** (CMS Coverage) — **24.4** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 83 | G2 0
103. **coda** (Coda) — **24.2** · D — disable candidate · MCP 24 | plat 43 | GH 0 | tech 0 | G2 54
104. **ahrefs** (Ahrefs) — **23.9** · D — disable candidate · MCP 24 | plat 38 | GH 0 | tech 0 | G2 57
105. **freshbooks** (FreshBooks) — **23.8** · D — disable candidate · MCP 24 | plat 37 | GH 0 | tech 0 | G2 57
106. **freshservice** (Freshservice) — **23.5** · D — disable candidate · MCP 24 | plat 32 | GH 0 | tech 0 | G2 61
107. **biorxiv** (bioRxiv) — **23.3** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 78 | G2 0
108. **sapsf** (SAP SuccessFactors) — **22.6** · D — disable candidate · MCP 24 | plat 40 | GH 0 | tech 49 | G2 0
109. **hex** (Hex) — **21.4** · D — disable candidate · MCP 24 | plat 37 | GH 0 | tech 0 | G2 46
110. **looker** (Looker) — **19.1** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 57
111. **onenote** (OneNote) — **18.2** · D — disable candidate · MCP 24 | plat 67 | GH 0 | tech 0 | G2 0
112. **anaplan** (Anaplan) — **17.9** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 51
113. **fireflies** (Fireflies) — **17.5** · D — disable candidate · MCP 52 | plat 35 | GH 0 | tech 0 | G2 0
114. **factset** (FactSet) — **17.4** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 48
115. **oracle_hcm** (Oracle HCM) — **17.3** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 48 | G2 0
116. **exa** (Exa) — **17.0** · D — disable candidate · MCP 50 | plat 35 | GH 0 | tech 0 | G2 0
117. **oracle_sales_cloud** (Oracle Sales Cloud) — **17.0** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 46 | G2 0
118. **ukg_pro** (UKG Pro) — **16.6** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 44 | G2 0
119. **hibob** (HiBob) — **16.0** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 41 | G2 0
120. **basecamp** (Basecamp) — **13.9** · D — disable candidate · MCP 24 | plat 45 | GH 0 | tech 0 | G2 0
121. **whoop** (WHOOP) — **13.4** · D — disable candidate · MCP 52 | plat 15 | GH 0 | tech 0 | G2 0
122. **oura** (Oura) — **13.1** · D — disable candidate · MCP 51 | plat 15 | GH 0 | tech 0 | G2 0
123. **pylon** (Pylon) — **12.8** · D — disable candidate · MCP 49 | plat 15 | GH 0 | tech 0 | G2 0
124. **lucidchart** (Lucidchart) — **12.7** · D — disable candidate · MCP 49 | plat 15 | GH 0 | tech 0 | G2 0
125. **crustdata** (Crustdata) — **11.4** · D — disable candidate · MCP 24 | plat 33 | GH 0 | tech 0 | G2 0
126. **rootly** (Rootly) — **11.0** · D — disable candidate · MCP 24 | plat 31 | GH 0 | tech 0 | G2 0
127. **foursquare** (Foursquare) — **10.8** · D — disable candidate · MCP 24 | plat 30 | GH 0 | tech 0 | G2 0
128. **gamma** (Gamma) — **10.8** · D — disable candidate · MCP 24 | plat 30 | GH 0 | tech 0 | G2 0
129. **adobe_pdf_services** (Adobe PDF Services) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
130. **amadeus** (Amadeus) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
131. **arize** (Arize) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
132. **articulate** (Articulate Reach 360) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
133. **aviationstack** (Aviationstack) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
134. **bitly** (Bitly) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
135. **compliancequest** (ComplianceQuest) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
136. **doordash** (DoorDash) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
137. **duffel** (Duffel) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
138. **firehydrant** (FireHydrant) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
139. **frameio** (Frame.io) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
140. **front** (Front) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
141. **guru** (Guru) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
142. **kintone** (Kintone) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
143. **oracle_scm** (Oracle SCM) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
144. **peec** (Peec AI) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
145. **quartr** (Quartr) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
146. **readme** (ReadMe) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
147. **sabre** (Sabre) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
148. **straker** (Straker) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
149. **teamwork** (Teamwork.com) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
150. **vestaboard** (Vestaboard) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0
151. **visualping** (VisualPing) — **7.8** · D — disable candidate · MCP 24 | plat 15 | GH 0 | tech 0 | G2 0

## Caveats

- Signals are **public proxies**, not measured Merge connect rates.
- Niche B2B tools (FactSet, Sabre, Oracle modules) score low on consumer-
  facing signals but may be critical for specific ICPs.
- Google Workspace connectors are split (gmail, drive, sheets, …) so each
  scores high individually; deduplication in the UI may be warranted.
- `microsoft_teams_gcc` inherits Teams scores; consider collapsing variants.
- Public/no-auth APIs (weather, wikipedia, pubmed) score on technographics
  only — useful but not typical SaaS integrations.
- Re-run periodically; MCP registry and G2 counts drift quickly.
- Regenerate with `python3 docs/_build_merge_connector_rankings.py`.

## Sources

- Zapier app directory default sort: https://zapier.com/apps
- Make/Pipedream top lists: https://stackreaction.com/integromat/integrations
- Composio toolkit catalog: https://docs.composio.dev/toolkits
- Smithery MCP registry: https://smithery.ai/servers
- PulseMCP / MCP Toplist: https://www.pulsemcp.com/servers , https://mcptoplist.com/
- G2 product pages (review counts, 2026-06)
- BuiltWith / W3Techs / industry reports for technographics (2025–2026)
- GitHub API / public repo stars (2026-06)

