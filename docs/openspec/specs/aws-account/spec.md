# AWS Account Connection

## Purpose

Enable secure cross-account access from DevOpsHero to customer AWS accounts for deployment purposes. Uses AWS AssumeRole with CloudFormation-provisioned IAM roles and external IDs to prevent confused deputy attacks.

## Requirements

### Requirement: AWSAccount Data Model

The system SHALL store AWS account connection state with the following fields:

- `organization` - Foreign key to the owning Organization
- `name` - User-friendly identifier (e.g., "Production", "Staging")
- `aws_account_id` - 12-digit AWS account ID (populated after verification)
- `external_id` - UUID for secure cross-account AssumeRole (generated at creation)
- `role_arn` - IAM role ARN that DevOpsHero assumes for deployments
- `status` - Connection state: PENDING, CONNECTED, or ERROR
- `status_message` - Additional status information or error details
- `created_by` - User who initiated the connection

#### Scenario: Account created in PENDING state

- **WHEN** a user initiates an AWS account connection
- **THEN** the system SHALL create an AWSAccount record with status PENDING
- **AND** generate a unique external_id UUID

#### Scenario: Account transitions to CONNECTED

- **WHEN** the CloudFormation callback reports successful stack creation
- **THEN** the system SHALL update status to CONNECTED
- **AND** populate aws_account_id and role_arn from the callback payload

#### Scenario: Account transitions to ERROR

- **WHEN** connection verification fails
- **THEN** the system SHALL update status to ERROR
- **AND** store error details in status_message

### Requirement: External ID for Confused Deputy Prevention

The system SHALL use external IDs to prevent confused deputy attacks when assuming cross-account roles.

The external_id MUST be:
- A UUID generated when the AWSAccount record is created
- Embedded in the customer's IAM role trust policy via CloudFormation
- Validated by AWS when DevOpsHero calls AssumeRole

#### Scenario: External ID prevents unauthorized access

- **WHEN** a malicious actor attempts to use another customer's IAM role ARN
- **THEN** the AssumeRole call SHALL fail because the external_id does not match the trust policy

#### Scenario: External ID embedded in CloudFormation

- **WHEN** generating the CloudFormation quick-create URL
- **THEN** the system SHALL include the external_id as a template parameter

### Requirement: Unique Name Constraint Per Organization

The system SHALL enforce unique AWS account names within each organization.

The constraint MUST be:
- Scoped to the organization (different organizations can use the same names)
- Enforced at the database level via unique constraint on (organization, name)

#### Scenario: Duplicate name rejected for same organization

- **WHEN** a user attempts to create an AWS account with a name that already exists in CONNECTED status within their organization
- **THEN** the system SHALL reject the request with a validation error

#### Scenario: Same name allowed across organizations

- **WHEN** two users in different organizations create accounts named "Production"
- **THEN** the system SHALL allow both accounts to be created

### Requirement: Status-Aware Duplicate Handling

The system SHALL allow retry for accounts in non-terminal states while preventing overwrite of connected accounts.

#### Scenario: PENDING account allows retry

- **WHEN** a user submits a name that matches an existing PENDING account
- **THEN** the system SHALL return the existing CloudFormation URL
- **AND** NOT create a duplicate record

#### Scenario: ERROR account allows retry

- **WHEN** a user submits a name that matches an existing ERROR account
- **THEN** the system SHALL return the existing CloudFormation URL
- **AND** NOT create a duplicate record

#### Scenario: CONNECTED account blocks creation

- **WHEN** a user submits a name that matches an existing CONNECTED account
- **THEN** the system SHALL return a validation error
- **AND** display message "An AWS account with this name is already connected"

### Requirement: CloudFormation URL Generation

The system SHALL generate AWS CloudFormation quick-create URLs that enable one-click stack deployment.

The URL MUST include:
- Stack name derived from the AWSAccount ID
- Template URL pointing to the DevOpsHero S3 bucket
- External ID as a template parameter
- Region parameter (us-east-1 default)

#### Scenario: URL generation for new account

- **WHEN** an AWSAccount record is created
- **THEN** the system SHALL be able to generate a valid CloudFormation quick-create URL
- **AND** the URL SHALL open the AWS Console to the stack creation page

#### Scenario: URL remains stable for existing account

- **WHEN** a user re-clicks "Open AWS Authorization Page" for an existing PENDING account
- **THEN** the system SHALL return the same CloudFormation URL

### Requirement: Callback Endpoint for Status Updates

The system SHALL expose an API endpoint to receive callbacks from the DevOpsHero install Lambda.

The endpoint MUST:
- Accept POST requests with Bearer token authentication
- Validate the external_id to identify the AWSAccount record
- Handle Create, Update, and Delete request types
- Return appropriate HTTP status codes for success and error cases

#### Scenario: Create callback marks account connected

- **WHEN** Lambda sends a Create callback with valid external_id
- **THEN** the system SHALL update status to CONNECTED
- **AND** populate aws_account_id and role_arn
- **AND** return HTTP 200 with success message

#### Scenario: Update callback refreshes role ARN

- **WHEN** Lambda sends an Update callback with valid external_id
- **THEN** the system SHALL update role_arn if changed
- **AND** return HTTP 200 with success message

#### Scenario: Delete callback marks account pending

- **WHEN** Lambda sends a Delete callback with valid external_id
- **THEN** the system SHALL update status to PENDING
- **AND** clear role_arn
- **AND** store "CloudFormation stack deleted by customer" in status_message

#### Scenario: Invalid authorization rejected

- **WHEN** a callback request has an invalid or missing Bearer token
- **THEN** the system SHALL return HTTP 401 Unauthorized

#### Scenario: Unknown external_id rejected

- **WHEN** a callback request has an external_id that does not match any AWSAccount record
- **THEN** the system SHALL return HTTP 404 Not Found

### Requirement: Input Validation

The system SHALL validate user input on both client and server sides.

#### Scenario: Empty name rejected client-side

- **WHEN** a user submits the form with an empty account name
- **THEN** the browser SHALL prevent form submission via HTML5 required attribute

#### Scenario: Empty name rejected server-side

- **WHEN** an empty name reaches the server (bypassing client validation)
- **THEN** the system SHALL return a validation error
- **AND** display inline error message

#### Scenario: Validation error styling

- **WHEN** a validation error occurs
- **THEN** the input field SHALL display a red outline
- **AND** error message SHALL appear inline
- **AND** space SHALL be reserved to prevent layout shift

### Requirement: Modal Behavior During Connection

The modal SHALL remain open after initiating the CloudFormation flow to provide context and feedback.

#### Scenario: Modal stays open after submission

- **WHEN** a user clicks "Open AWS Authorization Page"
- **THEN** the modal SHALL remain open
- **AND** display a "Waiting for CloudFormation" spinner

#### Scenario: Input becomes read-only

- **WHEN** a user has clicked "Open AWS Authorization Page"
- **THEN** the account name input SHALL become read-only (not disabled)
- **AND** the value SHALL still be submitted with subsequent form submissions

#### Scenario: Re-clicking opens same URL

- **WHEN** a user clicks "Open AWS Authorization Page" multiple times
- **THEN** the same CloudFormation URL SHALL be opened each time
