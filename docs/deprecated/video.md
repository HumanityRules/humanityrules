 ---
  Demo Video — Script & Storyboard

  Scene 1: The Pain (0:00–0:20)

  Screen: 

  First Part - Vibe-coding montage. Happy, fast.
  Second Part - Montage of Terraform files / Jira tickets / Slack messages (stock or mockup), cross-fading among them. Sad, slow.

  Narration:
    First Part - "Your team is building internal apps faster than ever. AI tools make it possible in hours."
    Second Part - "But then what? Getting that app deployed inside your company takes days — sometimes weeks. IAM policies, VPC networking, approval chains, SSO. The gap between 'it works on my laptop' and 'people can actually use it' saps your motivation."

  Notes:
  - The vibe-coding montage needs to feel real. Someone typing prompts into Cursor/Claude, code appearing, a local app running in the browser. Not abstract "AI" imagery. The viewer should think "yeah, that's me" or "that's my team."
  - The second part should feel like a wall. The cross-fades slowing down is good. Consider making the items feel like they're piling up — not just one Terraform file, but one after another. The feeling is accumulation, not just "this is hard." Maybe even a subtle visual of a clock or calendar advancing.

  Goal: Viewer feels the pain. No product shown yet.

  ---
  Scene 2: Meet DevOps Hero (0:20–0:30)

  Screen: Browser opens. Dashboard loads. We see the sidebar, the org name ("Course Hero"), a couple of existing apps on the dashboard.

  Narration:
  "This is DevOps Hero. After just a one-time 5 -minute setup — connecting your AWS account and GitHub — your company is ready to deploy. Let me show you what that looks like."

  Goal: Establish context. Plant the onboarding-is-easy claim. Transition to the demo.

  ---
  Scene 3: Start the deployment (0:30–0:50)

  Screen: Navigate to Workspace Detail ("Finance" workspace). Click "+ New App". Repository picker modal appears — list of GitHub repos. Select one (e.g., "simple-dashboard").
  
  Lands in the Deployment Editor.

  Narration:
  "I've got a Streamlit dashboard I built this morning. It's an internal app for the Finance department, with sensitive data. The Director of Finance granted me access to the Finance workspace. I pick the source code repository, and the AI takes it from here."

  ---
  Scene 4: AI analyzes and configures (0:50–1:30)

  Screen: The Deployment Editor, right panel. The AI agent starts working. We see tool calls appearing: "Read: src/Dockerfile", "Read: src/requirements.txt", "Analyze Repository".
   The agent's markdown messages appear — it identifies the app type, port, health check. The left panel updates in real-time as fields populate.

  Cross-fade through the slower parts. Show the key moments:
  1. Agent reads the code (tool calls with green checkmarks, timing)
  2. Agent proposes configuration ("I detected a Streamlit app on port 8501...")
  3. Agent asks which environment — interactive buttons appear: "Production" / "dev"
  4. User clicks "Production"

  Narration:
  "Devops Hero understands the app structure, generates the Dockerfile, and configures everything; secrets, health check, DNS domain... It asks where to deploy. I choose... Production"

  Then, as the deploy starts:
  "Behind the scenes, it's generating infrastructure as code — a Fargate service in a private VPC, least-privilege IAM roles, ALB routing, TLS certificates. This would normally be days of tickets to the DevOps team." 

  Goal: The AI doing real work, visibly. Tool calls build trust. The left panel updating in real-time is the visual payoff.

  ---
  Scene 5: The "holy shit" moment (1:30–1:50)

  Screen: Cross-fade through the deployment progress. Then: the App Detail page. Status pill turns green: "Succeeded". A live URL appears: https://simple-dashboard.chsandbox.com.
  Click it — the Streamlit dashboard opens in the browser, running, accessible.

  Narration:
  "And... it's live. A real app, running my company's AWS account, following the company's security policies. Two minutes, no infrastructure tickets, no Terraform."

  Let it breathe. A beat of silence while the viewer sees the running app.

Scene 6: App permissions (1:50–2:10)
  Screen: The Permissions Editor, split panel. Left shows IAM policy statements — S3 read-only, DynamoDB scoped to one table. Right shows the AI chat that generated them.
  Narration: "The AI analyzed the code and generated a least-privilege IAM policy. The app can read from S3 and access one DynamoDB table — nothing more. And these permissions went through an approval workflow before they were applied."

  ---
  Scene 7: Audit trail (2:25–2:35)
  Screen: App Detail, recent deployments table. Then quick cut to Security Hub, permission request log.
  Narration: "Every deployment, every permission change — who did it, when, and why. The audit trail your compliance team needs, generated automatically."

---
  Scene 8: Who can deploy where (2:10–2:25)
  Screen: Security > Groups page — show "Finance", "Engineering", "Contractors" with their attribute tags. Quick cut to Policies — the rule in plain English.
  Narration: "The Finance team can deploy to Finance workspaces. [confident] Contractors can view but not deploy. [firm] These rules come from group attributes and policies, [matter-of-factly]not from someone manually granting permissions [relieved]one by one."

---
  Scene 8: Who can deploy where (2:10–2:30)

  Screen flow (3 quick cuts):
  1. Security > Groups — show the "Finance" group card: 12 members, attributes department=finance, clearance=internal. Next to it, "Contractors" group: clearance=external.
  2. Security > People — show a user row with color-coded attribute pills inherited from their group: department=finance, clearance=internal, org-role=member.
  3. Security > Policies — show the rule in plain English: IF identity department=finance AND workspace workspace-name=finance THEN workspace:edit, environment:deploy.

  Narration:
  Remember how the Director of Finance gave me access? [curious]Here's what that actually looks like. [revealing]I'm in the Finance group with internal clearance. A policy says: if your department is finance and your clearance is internal, you can deploy to this workspace. A contractor on the same team? [emphatic] They can't — wrong clearance. [curious]And Production? [emphatic]Only internal employees who are also org admins can deploy to environments tagged as production. [proud] You define the rules once. The system enforces your policy everywhere.

  ---
  Scene 9: Closing (2:30–2:45)

  Screen: DevOps Hero logo, tagline, CTA.

  Narration:
  "Your people are already building apps with AI. DevOps Hero gets those apps into production — in minutes, with enterprise governance."
  
  Screen: "Book a demo" with URL/button.

  Narration
    "You need to see it. Book a demo."

---------------------------


  For the recording, you'll need:

  To record yourself:
  - The full deploy flow end-to-end (Scenes 2–5). Do it in one take against real data. Don't worry about pacing — you'll cut in editing. Do multiple takes if needed.
  - The epilogue screens (Scene 6) — just navigate to each one and pause for a few seconds. These are static shots you'll cut together.

  For the Scene 1 montages:

  Part 1 (vibe-coding):
  - You could screen-record yourself using Cursor/Claude for 5 minutes and cut the best moments. This would be the most authentic. Real keystrokes, real prompts, real code appearing.

  Part 2 (the wall):
  - Terraform files — screenshot some open-source .tf files, or write a few realistic-looking ones
  - Jira/Linear tickets — mock up a few with titles like "Request VPC access for dashboard app", "IAM role setup for ECS task"
  - Slack messages — mock up a thread like "hey @platform-team, can someone provision this?" with no reply for 3 days

  AI video generators (Runway, Kling, Pika) are decent for abstract/atmospheric shots but bad at UI screens and text. They'd work for a mood background behind text overlays, but  not for realistic-looking Jira tickets or code editors. For the montages, static screenshots with cross-fades and subtle zoom (Ken Burns effect) will look better and be much easier to produce.




[thoughtful]Your team is building internal apps faster than ever. [excited]AI tools make it possible, in hours! 

[Doubtful] But then what? [sighs] Getting that app deployed inside your company takes days, sometimes weeks. [muttering][fast] Permissions, approvals, single-sign on configuration. 

[sad] The gap between 'it works on my laptop' and 'people can actually use it' saps your motivation.

[Epic]This [slight pause]is Devops Hero

[Normal]After just a one-time 5-minute setup connecting your AWS account and Git, your company is ready to deploy. [Faster] Let me show you what that looks like.

[confident]I've got a Streamlit dashboard I built this morning. 

[excited]It's an internal app for the Finance department, [very cautious]with sensitive data. The Director of Finance granted me access to the Finance workspace. 

[thoughtful]I pick the source code repository, [excited]and the AI takes it from here."

[excited]Devops Hero understands the app structure, generates the Dockerfile, and configures everything; secrets, health check, DNS domain... [Delighted]It asks where to deploy. 

I choose... [confident]Production. [reassuring]Not everyone can deploy here — only users with the right clearance level have access to production environments.

[informative]Behind the scenes, Devops Hero generates infrastructure as code — a Fargate service in a private VPC, least-privilege IAM roles, load balancer routing, certificates... This would normally be days of tickets to the DevOps team.

[Anticipating]And... [Celebrating]it's live. A real app, running my company's AWS account, following the company's security policies. 

[Epic]Two minutes, no infrastructure tickets, no Terraform.

Devops Hero analyzed the code and generated a least-privilege permission policy. Our app can read from S3 and access one DynamoDB table — [emphasis]nothing more. 

[emphasis][reassuring]And these permissions went through an approval workflow before they were applied.

Every deployment, every permission change — [measured]who did it, when, and why. [reassured]The audit trail your compliance team needs, [excited]generated automatically.

Remember how the Director of Finance gave me access? [curious]Here's what that actually looks like. [revealing]I'm in the Finance group with internal clearance. A policy says: if your department is finance and your clearance is internal, you can deploy to this workspace. A contractor on the same team? [emphatic] They can't — wrong clearance. [curious]And Production? [emphatic]Only internal employees who are also org admins can deploy to environments tagged as production. [proud] You define the rules once. The system enforces your policy everywhere.

Your people are already building apps with AI. [triumphant] DevOps Hero gets those apps into production — in minutes, with enterprise governance.

[sincere]You need to see it. [inviting]Book a demo.