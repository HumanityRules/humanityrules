from django.contrib.auth import login
from django.db import transaction
from django.shortcuts import render, redirect
from django.utils.text import slugify

from ..models import User, Organization, OrganizationMembership
from ..services import abac


def onboarding(request):
    """
    Handles new user onboarding - collects organization name and creates
    User + Organization + Membership in a single transaction.
    """
    # If already logged in, go to dashboard
    if request.user.is_authenticated:
        return redirect("/dashboard/")
    
    # Must have pending WorkOS user data from auth callback
    pending_user = request.session.get("pending_workos_user")
    if not pending_user:
        return redirect("/auth/login/")
    
    if request.method == "POST":
        org_name = request.POST.get("organization_name", "").strip()
        
        if org_name:
            # Generate unique slug
            base_slug = slugify(org_name)
            slug = base_slug
            counter = 1
            while Organization.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            
            # Create everything in a single transaction
            with transaction.atomic():
                org = Organization.objects.create(name=org_name, slug=slug)
                
                user = User.objects.create(
                    workos_user_id=pending_user["workos_user_id"],
                    email=pending_user["email"],
                    username=pending_user["email"],
                    first_name=pending_user["first_name"],
                    last_name=pending_user["last_name"],
                    current_organization=org,
                )
                
                OrganizationMembership.objects.create(
                    user=user,
                    organization=org,
                    role=OrganizationMembership.Role.ADMIN,
                )

                abac.bootstrap_organization(organization=org, admin_user=user)
            
            # Clear session data and log in
            del request.session["pending_workos_user"]
            login(request, user)
            
            return redirect("/dashboard/")
    
    return render(request, "devopshero_app/onboarding.html", {
        "email": pending_user["email"],
    })

