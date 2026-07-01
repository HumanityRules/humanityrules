# Uniform org-scoped resource naming

## Goal

Make every AWS resource name carry the slug of the **org that owns it**, so that one AWS
account can host many organizations' resources without name collisions, without cross-org
access, and with every resource attributable to a tenant. This replaces today's
`humr-<env>-…` scheme, where per-app resources are named only by env + app and therefore
collide across orgs the moment two tenants share an account (the shared sandbox).

Naming is the substrate that makes account-sharing *clean and attributable*. It is **not**
isolation — see the last section.

## The model

Everything is `humr-<org>-<env>-<app>`. The only thing that varies is *which* org owns a
given resource:

- **Owned per-app resources** — `humr-<org>-<env>-<app>-*` (dash names: stacks, ECS
  service/task-def, target group, roles, etc) and `humr/<org>/<env>/<app>/…` (slash names:
  Secrets Manager, ECR, etc).
- **Filesystem/host paths** — `/deployments/<org>/<app>` (EFS) and
  `/var/lib/humr/hermes-roots/<org>/<app>` (EC2 host). Env is implicit — the resource *is*
  that env's filesystem/host — so it isn't repeated in the path.
- **Base infra** (VPC, ALB, cluster, EFS filesystem, builder, etc) is always owned by an org.
   So it is `humr-<org>-<env>-vpc`, etc. There is no ownerless infra: the schema is uniform,
  only the owner differs. In a dedicated single-tenant account the owner is the tenant, so base 
  infra and apps share one prefix. In the HumR sandbox account, the base infra is 
  `humr-<platform-org>-<env>-vpc`, where platform-org is settings.HUMR_PLATFORM_OWNER_ORG_SLUG.
- **DNS** — dedicated account: `<app>.<customer-domain>` (unchanged). Shared sandbox:
  `<app>.<org>.<sandbox-zone>` (e.g. `app.org.humr.io`), each org-zone with its own wildcard cert.

**Why org-first.** Org is above env in the data model (`Environment → AWSAccount →
Organization`); the name mirrors real ownership/containment. It also lets a single
`humr-<org>-*` prefix scope a tenant for IAM policies and cost allocation.

This is collision-free for free: `Organization.slug` is globally unique and `App.slug` is
unique per org, so `(org, app)` is already globally unique. No reservation table is needed.

## Length-bound names use a deterministic short id

A few AWS names have limits too tight to hold org + env + app readably. For example:

- Target group — 32 chars
- IAM task role — 64 chars
- Aurora cluster identifier — 63 chars

**Verify this set as you implement** — there may be others. For *only* these, leave the app name
 and use a hash:

```
rid = sha256(f"{org_slug}/{env_slug}/{app_slug}").hexdigest()[:12]
```

→ `humr-<app>-<rid>`, `humr-<app>-<rid>-task-role`, `humr-<app>-<rid>-aurora`. These are order-neutral and
identified by tags. Every other resource stays readable.

## Tags (required on every resource)

`Org=<org_slug>`, `Env=<env_slug>`, `App=<app_slug>`, `humr:rid=<rid>`. These make the
hashed names identifiable and enable tenant grouping/cost allocation independent of the name.
The infra already tags `App`; extend it to all four, everywhere.

## Slug constraints

`org.slug` and `app.slug` become **immutable** (they appear in every resource name) and
should be **length-capped** (~30 chars each) so readable names stay within limits and a
single DNS label (63 chars) holds `<org>-<app>`. A mutable display name covers any UX need
for renaming.

## Implementation guidance

- **Centralize the naming.** Today resource names are scattered f-strings across the infra
  layer. Introduce one naming module that derives every name — plus the `rid` and the tag
  set — from `(org_slug, env_slug, app_slug)`, and base-infra names from
  `(platform_org_slug, env_slug)`. Then change call sites to use it. The main plumbing work
  is threading `org_slug` into the naming inputs (the AppConfig carries `app_name` but not
  the org today).

- **Retire the `SandboxSlugClaim` mechanism.** It exists only because resources were not
  org-namespaced; once they are, a cross-org slug collision is impossible and the reservation
  is dead weight. Remove the `SandboxSlugClaim` model and its migration, the
  `aclaim_sandbox_app_slug` / `release_sandbox_app_slug` helpers in `sandbox_service`, and
  their call sites in the template-deploy, agent deploy-blueprint, and app-remove paths. The
  per-org DB uniqueness on `App.slug` stays.

- **De-special-case the secrets code.** The per-org namespacing of the env-bearer / shared
  secrets (the `env_shared_secrets_namespace` carve-out in `secrets_utils`) was a one-off fix
  for the secret names specifically. Under uniform org-naming it is no longer special —
  secrets are just `humr/<org>/<env>/…` like everything else. Fold it into the general path
  and delete the carve-out.

## Renaming sequence

Rename the 

## Rollout

Do not rollout anything yet. We will do it together after the rename is done.

## What this is NOT: isolation

Naming gives collision-freedom, attribution, and the *ability* to write org-scoped IAM. It
does not isolate tenants. Safely sharing one account still requires, separately:

- **Network** — tenants currently share one VPC and security group.
- **Host/compute** — different tenants' containers (and their `hermes-roots` host
  bind-mounts) are co-resident on the same EC2 hosts and kernel.
- **IAM** — org-prefixed names let you scope a task role to `humr-<org>-*`, but the policies
  still have to be authored and verified.

These are out of scope for this document and task.


## To whoever implements this

This is the intended design and the reasoning behind it — not a frozen spec. As you load the
code, if you see a materially better approach, or a rename site or a length limit I got
wrong, say so before implementing.
