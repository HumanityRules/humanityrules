---
name: aws-cli
description: Use the AWS CLI from inside a Hermes sandbox. Covers the local signer/proxy failure pattern where STS works but another AWS service returns token-invalid errors.

version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [AWS, CLI, Credentials, Sandbox, Debugging]
---


## Hermes-specific credential model

The sandbox does not receive long-lived AWS credentials. AWS calls go through a
local signer/proxy that injects short-lived credentials for the sandbox task
role.

Supported signer services:

- `sts` -> `127.0.0.1:9901` -> `sts.amazonaws.com`
- `bedrock` -> `127.0.0.1:9902` -> `bedrock.${AWS_DEFAULT_REGION}.amazonaws.com`
- `bedrock_runtime` -> `127.0.0.1:9903` -> `bedrock-runtime.${AWS_DEFAULT_REGION}.amazonaws.com` signed as `bedrock`
- `cost_explorer` -> `127.0.0.1:9904` -> `ce.us-east-1.amazonaws.com`
- `s3` -> `127.0.0.1:9905` -> `s3.${AWS_DEFAULT_REGION}.amazonaws.com` (path-style; buckets must live in the deploy region — cross-region 301s are not followed)
- `s3tables` -> `127.0.0.1:9906` -> `s3tables.${AWS_DEFAULT_REGION}.amazonaws.com`

Learned failure pattern: `aws sts get-caller-identity` and Bedrock succeeded
while services outside this signer list returned
`UnrecognizedClientException` or `InvalidClientTokenId` with the same session.
In this environment that usually means the request reached AWS without a
broker-injected signature, not that the user handed the agent bad credentials.

## Broker coverage probe

Run this before asking for IAM policy changes when STS works but another service
returns a token-invalid error:

```bash
aws sts get-caller-identity

for h in sts.amazonaws.com bedrock.us-east-1.amazonaws.com bedrock-runtime.us-east-1.amazonaws.com; do
  printf "%-45s " "$h"
  curl -s -o /dev/null -w "HTTP=%{http_code} time=%{time_total}s\n" --max-time 5 "https://$h/"
done
```

In the incident that produced this skill, STS and Bedrock answered in roughly
12-20 ms from us-east-1 endpoints, which identified a local proxy path.

## Sandbox-specific cautions

- Do not run `aws configure`, `aws sso login`, or set
  `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in the sandbox. The existing CLI
  setup is part of the broker path.
- Do not ask for IAM policy changes when a successful STS call is followed by
  `UnrecognizedClientException` / `InvalidClientTokenId` until the broker probe
  has ruled out unsigned forwarding.
