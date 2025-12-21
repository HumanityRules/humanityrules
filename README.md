# Project synopsis

**Devops Hero** is a platform designed to make deploying internal tools to a company's private cloud (VPC) as easy as using Heroku, Render, or Railway, while maintaining enterprise security and compliance.

- **The Problem:** While AI has made building apps faster than ever, deploying them internally is still a bottleneck due to complex requirements like IAM permissions, SSO integration, VPC networking, and compliance checks.
- **The Solution:** An AI-powered "co-pilot" that automates the deployment process and governance, allowing developers to ship internal apps in minutes rather than weeks.
- **Key Features:**
    - **Automated Infrastructure:** Deploys directly to your company’s VPC without requiring manual Terraform or YAML wrestling.
    - **AI-Assisted Security:** Uses an AI wizard to configure IAM permissions and set up approval chains.
    - **Built-in SDK:** Provides out-of-the-box integration for SSO, Role-Based Access Control (RBAC), and standardized logging/metrics.
    - **Governance:** Includes approval flows for sensitive changes to ensure compliance.
- **Target Audience:** It aims to empower Full Stack Engineers, Data Scientists, Machine Learning Engineers, and Business staff to "vibe-code" and ship tools independently, while giving DevOps teams the control and standardization they need.


# What I did to boostrap the project
```
uv init .
uv add django==6.0
uv run django-admin startproject devopshero_site .
uv run manage.py startapp devopshero_app
uv run manage.py migrate
uv run manage.py runserver
```

# Python style guide

## Function Signatures & Calling Conventions

To prioritize readability and eliminate "magic" behavior, we enforce strict explicitness in both how functions are defined and how they are invoked.

### 1. Enforce Explicit Argument Passing
* **Directive:** Do not use default parameter values in function or method definitions. All parameters must be mandatory.
* **Reasoning:** "Magic defaults" hide complexity and obscure the function's dependencies. We prefer explicit calls where every argument is visible at the call site.
* **Implementation:** If a value is logically optional, the caller must explicitly pass `None`.

**Bad (Implicit Defaults):**
```python
def connect_to_db(url, retries=3, timeout=30):
    # Hidden behavior: Reader assumes 0 retries? Infinite timeout?
    ...
```

**Good (Explicit Arguments):**
```python
def connect_to_db(url, retries, timeout):
    # All dependencies are visible in the signature
    ...
```

### 2. Prefer Keyword Arguments
* **Directive:** Use named arguments (keyword arguments) for function calls, particularly when passing literals (numbers, booleans, or strings) or when a function takes multiple parameters.
* **Reasoning:** Positional arguments are brittle and often illegible (the "Boolean Trap"). Keyword arguments make the code self-documenting and prevent errors caused by parameter reordering.
* **Implementation:** Explicitly state the parameter name at the call site.

**Bad (Positional Ambiguity):**
```python
# The reader cannot know what '3' and '30' represent
connect_to_db("db://localhost", 3, 30)

# Confusing booleans
create_user("jdoe", True, False)
```

**Good (Self-Documenting):**
```python
# Clarity is enforced at the call site
connect_to_db(url="db://localhost", retries=3, timeout=30)

# Intent is obvious even for optional flows
connect_to_db(url="db://localhost", retries=None, timeout=None)

# Boolean flags are readable
create_user(username="jdoe", is_admin=True, send_email=False)
```
