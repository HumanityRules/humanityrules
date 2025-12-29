# TODO: Onboarding Flow

## High Priority

- [ ] **Form validation and error handling** - Display validation errors on the onboarding page (empty org name, slug conflicts, etc.)

- [ ] **Handle session expiration** - If the user closes the browser mid-onboarding and returns later, the session data is lost. Consider:
  - Storing pending user data in a temporary DB table with expiration
  - Showing a friendly message when session is missing instead of silent redirect

## Medium Priority

- [ ] **Support invitation flow** - Allow new users to join an existing organization via invite link instead of always creating a new one

- [ ] **Sync WorkOS organization data** - If WorkOS returns organization info from SSO/directory sync, use that instead of asking user to create one

- [ ] **Add organization slug preview** - Show the user what their org's URL slug will be as they type the name

- [ ] **Loading state on form submission** - Disable submit button and show spinner while creating user/org

## Low Priority

- [ ] **Add "Sign in with a different account" link** - On the onboarding page, let users go back to sign in with a different WorkOS account

- [ ] **Analytics/tracking** - Track onboarding completion rate, drop-off points

- [ ] **Welcome email** - Send a welcome email after successful onboarding

- [ ] **Onboarding tour** - After first login, show a guided tour of the dashboard

## Technical Debt

- [ ] **Slug uniqueness race condition** - The current slug generation has a TOCTOU race. Consider using a unique constraint with retry logic or DB-generated slugs

- [ ] **Add tests for onboarding flow** - Unit tests for `auth_callback` branching, `onboarding` view, and transaction atomicity



# TODO: AWS Accounts
- [ ] Background job to verify AssumeRole works
- [ ] "Send instructions" email flow
- [ ] Create new AWS Account flow
- [ ] Delete/disconnect flow with CloudFormation stack cleanup guidance
- [ ] Support for AWS Organizations (multiple accounts under one root)


# Customer Installation
- Least privilege of the role we create in the customer's account
- All our resources must be tagged: doh:awsAccountId, doh:createdBy, doh:environmentId, doh:id, doh:organizationId, doh:projectId, doh:purpose
