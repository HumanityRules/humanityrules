"""
VPC utility functions for, for example, finding available CIDR ranges in a customer account.
"""

import ipaddress


def cidrs_overlap(cidr1: str, cidr2: str) -> bool:
    """Check if two CIDR blocks overlap."""
    net1 = ipaddress.ip_network(cidr1, strict=False)
    net2 = ipaddress.ip_network(cidr2, strict=False)
    return net1.overlaps(net2)


def find_available_vpc_cidr(ec2_client) -> dict:
    """
    Find an available /20 CIDR in the 172.16-31.x.x range that doesn't 
    conflict with existing VPCs.
    
    Returns a dict with VpcCidr, PublicSubnet1Cidr, PublicSubnet2Cidr,
    PrivateSubnet1Cidr, PrivateSubnet2Cidr.
    """
    print("\n🔍 Scanning for available CIDR range...")
    
    # Get all existing VPC CIDRs in the account
    vpcs = ec2_client.describe_vpcs()
    used_cidrs = []
    for vpc in vpcs["Vpcs"]:
        used_cidrs.append(vpc["CidrBlock"])
        # Also check associated CIDR blocks (VPCs can have multiple)
        for assoc in vpc.get("CidrBlockAssociationSet", []):
            if assoc.get("CidrBlock"):
                used_cidrs.append(assoc["CidrBlock"])
    
    if used_cidrs:
        print(f"   Found existing VPC CIDRs: {', '.join(used_cidrs)}")
    else:
        print("   No existing VPCs found")
    
    # Try /20 blocks in 172.16.0.0/12 range (172.16.0.0 - 172.31.255.255)
    # We'll try 172.20.0.0/20, 172.20.16.0/20, 172.20.32.0/20, etc.
    # Then 172.21.x.x, 172.22.x.x, up to 172.31.x.x
    for second_octet in range(20, 32):  # 172.20 through 172.31
        for third_octet in range(0, 256, 16):  # /20 = 16 in third octet
            candidate = f"172.{second_octet}.{third_octet}.0/20"
            
            # Check for overlap with any existing CIDR
            has_conflict = False
            for used in used_cidrs:
                if cidrs_overlap(candidate, used):
                    has_conflict = True
                    break
            
            if not has_conflict:
                # Found a good one! Calculate subnet CIDRs
                # /20 gives us 4096 IPs, we'll carve out four /24 subnets:
                # - 2 public (for NAT Gateway and potential ALB)
                # - 2 private (for Fargate tasks)
                vpc_net = ipaddress.ip_network(candidate)
                subnets = list(vpc_net.subnets(new_prefix=24))
                
                result = {
                    "VpcCidr": candidate,
                    "PublicSubnet1Cidr": str(subnets[0]),   # .0.0/24
                    "PublicSubnet2Cidr": str(subnets[1]),   # .1.0/24
                    "PrivateSubnet1Cidr": str(subnets[2]),  # .2.0/24
                    "PrivateSubnet2Cidr": str(subnets[3]),  # .3.0/24
                }
                print(f"   ✅ Selected: {candidate}")
                return result
    
    raise Exception("No available CIDR range found in 172.16-31.x.x")

