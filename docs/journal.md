# DevOpsHero Development Journal

## 2025-12-26 - AWS Infrastructure Setup for Cross-Account Access

### Summary

Today we built the AWS infrastructure that allows DevOpsHero to connect to customer AWS accounts. The system uses CloudFormation to create IAM roles in customer accounts, with a callback mechanism to automatically notify DevOpsHero when a customer completes the setup.

### What We Built

#### 1. Install Callback Lambda (`cf_install_callback_lambda.json` + `install_callback_lambda.py`)

We created a Lambda function that acts as a CloudFormation Custom Resource handler. When a customer deploys our CloudFormation template in their AWS account, this Lambda is automatically invoked to notify the DevOpsHero backend.

**Why:** Without this callback, customers would have to manually provide their AWS Account ID after deploying the stack, and we'd have no confirmation the deployment actually succeeded. The callback automates this—CloudFormation itself tells us the deployment completed and provides the account ID and Role ARN directly.

#### 2. S3 Buckets (`cf_public_bucket.json` + `cf_private_bucket.json`)

We created two S3 buckets:

- **devopshero-public**: Hosts the customer-facing CloudFormation template (`cf_install_template.json`). Must be public so AWS Console can fetch it via the quick-create URL.
- **devopshero-private**: Stores the Lambda code zip file. Private because it contains internal implementation details.

Both buckets have versioning enabled for rollback capability.

#### 3. Customer Install Template (`cf_install_template.json`)

The CloudFormation template that customers deploy in their AWS accounts. It creates:
- An IAM role with `AdministratorAccess` that DevOpsHero can assume
- A custom resource that calls our callback Lambda

**Security:** Uses an `ExternalId` parameter to prevent confused deputy attacks. Each customer gets a unique ExternalId stored in our database.

#### 4. Deployment Scripts

- `run_devops_deployment.sh`: Master script that deploys all infrastructure in the correct order
- `upload_s3_files.sh`: Uploads Lambda code and install template to S3
- `update_install_callback_lambda.sh`: Quick script to update just the Lambda code

### Technical Decisions

**Why separate the Lambda code into a .py file?**
Originally the Python code was embedded in the CloudFormation template using `ZipFile`. Extracting it to `install_callback_lambda.py` makes the code easier to read, edit, and test. The tradeoff is we now need S3 to host the zip file.

**Why two buckets instead of one?**
Security principle of least privilege. The public bucket only contains the install template (which customers need to see anyway). The Lambda code stays private.

**Chicken-and-egg problem:**
The Lambda needs its code in S3, but S3 must exist first. We solved this by ordering the deployment script:
1. Create buckets
2. Upload files to S3
3. Deploy Lambda

### Issues We Encountered

1. **Invalid RetentionInDays**: CloudWatch Logs only accepts specific values (1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, etc.). We tried 768, had to change to 731.

2. **ROLLBACK_COMPLETE state**: When a CloudFormation stack fails during creation, it enters this state and cannot be updated—only deleted. Added delete-and-wait logic to the deployment script.


### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Customer's AWS Account                       │
│                                                                 │
│  CloudFormation Stack                                           │
│  ├── IAM Role (devopshero-{external_id})                        │
│  │   └── Allows DevOpsHero account to AssumeRole                │
│  └── Custom Resource ──────────────────────────────────────┐    │
│                                                            │    │
└────────────────────────────────────────────────────────────│────┘
                                                             │
                                                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   DevOpsHero AWS Account (555553041615)         │
│                                                                 │
│  ┌─────────────────┐    ┌──────────────────────────────────┐    │
│  │ S3 (public)     │    │ Lambda: devopshero-install-callback│  │
│  │ - install tpl   │    │                                    │  │
│  └─────────────────┘    │ Receives: AccountId, RoleArn,      │  │
│                         │           ExternalId, Region       │  │
│  ┌─────────────────┐    │                                    │  │
│  │ S3 (private)    │    │ Calls: DevOpsHero Backend API      │  │
│  │ - lambda code   │    └───────────────────────────────────┘   │
│  └─────────────────┘                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### TODO

- Implement the backend API endpoint `/api/aws/account-callback` to receive Lambda callbacks
- Add error handling in the callback Lambda for network failures (retries?)
- Consider adding SNS notifications for failed stack deployments
- Test the full flow with code
- Add CloudWatch alarms for Lambda errors
- Document the customer onboarding flow
- Reduce IAM permissions from AdministratorAccess to least-privilege (later, once we know exactly what's needed)
- Rearchitecture DOH infra stack to use nested stacks, while solving the chicken and egg problem between S3 and 
  lambda code by keeping the private bucket in its own independent stack.
