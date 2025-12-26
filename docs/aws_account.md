# AWS Account Connection

This document describes how DevOpsHero connects to customer AWS accounts for deployment purposes.

## Overview

DevOpsHero deploys applications to customer AWS accounts. To do this securely, we use AWS's **cross-account AssumeRole** mechanism via CloudFormation. Customers create a CloudFormation stack in their AWS account that provisions an IAM role. DevOpsHero then assumes this role to perform deployments.

## Data Model

### AWSAccount Model

```python
class AWSAccount(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"    # Waiting for CloudFormation stack
        CONNECTED = "connected" # Successfully verified
        ERROR = "error"        # Verification failed

    organization = ForeignKey(Organization)
    name = CharField(max_length=255)           # User-friendly name
    aws_account_id = CharField(max_length=12)  # 12-digit AWS account ID
    external_id = UUIDField(default=uuid.uuid4) # Security: prevents confused deputy
    role_arn = CharField(max_length=2048)      # IAM role ARN to assume
    status = CharField(choices=Status.choices)
    status_message = TextField()               # Error details if applicable
    created_by = ForeignKey(User)
```

### Key Design Decisions

#### 1. External ID (Confused Deputy Prevention)

The `external_id` is a UUID generated when the AWSAccount record is created. It's embedded in both:
- DevOpsHero's database (tied to the specific customer)
- The customer's IAM role trust policy (via CloudFormation)

**Why?** Without external IDs, a malicious actor could:
1. Sign up for DevOpsHero
2. Provide a victim's IAM role ARN
3. Trick DevOpsHero into performing actions in the victim's account

With external IDs, each customer has a unique secret. When DevOpsHero calls `AssumeRole`, AWS validates that the external ID matches the one in the trust policy. A malicious actor cannot know another customer's external ID.

#### 2. Unique Name Constraint Per Organization

```python
constraints = [
    models.UniqueConstraint(
        fields=["organization", "name"],
        name="unique_aws_account_name_per_org",
    )
]
```

**Why?** Users identify accounts by name (e.g., "Production", "Staging"). Duplicate names within an organization would cause confusion. The constraint is per-organization, not global, so different organizations can use the same names.

#### 3. Status-Based Validation

When a user tries to create an account with an existing name:
- **PENDING or ERROR**: Allow it — return the existing CloudFormation URL so they can retry
- **CONNECTED**: Block it — show validation error "already connected"

**Why?** Users may need to retry the CloudFormation setup if it failed or they closed the browser. But we don't want them accidentally overwriting a working connection.

## User Flow

### Entry Point

Settings → AWS Accounts tab → "Add AWS Account" button

### Connection Modal

The modal is a single-page dialog with:

1. **AWS Account Name input** — User-friendly identifier
2. **Connection method radio buttons**:
   - "I have Administrator access" — Direct CloudFormation setup
   - "Send instructions to someone" — Email delegation (future)
   - "Create new AWS account" — Links to AWS signup first

3. **Step-by-step instructions** with "Open AWS Authorization Page" button
4. **Info sidebar** explaining security, revocation, pricing, regions

### State Transitions

```
[User opens modal]
        ↓
[User enters name, clicks "Open AWS Authorization Page"]
        ↓
[POST /settings/aws-accounts/add/]
        ↓
[AWSAccount created with status=PENDING]
        ↓
[CloudFormation URL opened in new tab]
        ↓
[Modal stays open with "Waiting for CloudFormation" spinner]
        ↓
[User creates stack in AWS Console]
        ↓
[Future: Webhook or polling verifies connection → status=CONNECTED]
```

### Modal Behavior Decisions

#### Stay Open After Submit

**Decision**: The modal stays open after clicking "Open AWS Authorization Page".

**Why?** Creating the CloudFormation stack takes several minutes. Closing the modal would:
- Lose context of what the user was doing
- Make it harder to retry if something goes wrong
- Not provide feedback about the waiting state

#### Input Becomes Read-Only

**Decision**: The account name input becomes read-only (not disabled) after submission.

**Why?** 
- `readonly` still submits the value with the form (allows re-clicking the button)
- `disabled` does NOT submit the value (would break subsequent requests)
- Visual feedback that the name is "locked in"

#### Re-clicking Opens Same URL

**Decision**: Clicking "Open AWS Authorization Page" multiple times reopens the same CloudFormation URL.

**Why?** Users may:
- Accidentally close the AWS tab
- Need to see the CloudFormation page again
- Want to verify the stack parameters

The server uses `get_or_create` pattern — if the account exists, return its URL; if not, create it.


## Validation

### Client-Side
- HTML5 `required` attribute on name input
- Error styling (red outline) on validation failure
- Error message appears inline, space reserved to prevent layout shift

### Server-Side
- Empty name check
- Duplicate name check (with status-aware logic)
- Database unique constraint as final safety net
