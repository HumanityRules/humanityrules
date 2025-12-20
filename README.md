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

uv init .
uv add django==6.0
uv run django-admin startproject devopshero_site .
uv run manage.py startapp devopshero_app
uv run manage.py migrate
uv run manage.py runserver