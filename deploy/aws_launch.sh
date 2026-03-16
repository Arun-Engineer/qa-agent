#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  AWS EC2 — Quick Launch Commands
#  Run these from your LOCAL machine (not EC2)
#  Prerequisite: aws cli configured (aws configure)
# ═══════════════════════════════════════════════════════════════

# ═══ VARIABLES — EDIT THESE ═══
REGION="ap-south-1"           # Mumbai (closest to Chennai)
KEY_NAME="qa-agent-key"       # SSH key pair name
INSTANCE_TYPE="t2.micro"      # Free tier eligible
AMI_ID="ami-0f5ee92e2d63afc18"  # Ubuntu 22.04 LTS (ap-south-1) — update if different region

echo "=== Step 1: Create Security Group ==="
SG_ID=$(aws ec2 create-security-group \
    --group-name qa-agent-sg \
    --description "AI QA Platform" \
    --region $REGION \
    --output text --query 'GroupId')
echo "Security Group: $SG_ID"

# Allow SSH (your IP only — more secure)
MY_IP=$(curl -s https://checkip.amazonaws.com)
aws ec2 authorize-security-group-ingress --group-id $SG_ID --region $REGION \
    --protocol tcp --port 22 --cidr "$MY_IP/32"

# Allow HTTP + HTTPS (public)
aws ec2 authorize-security-group-ingress --group-id $SG_ID --region $REGION \
    --protocol tcp --port 80 --cidr "0.0.0.0/0"
aws ec2 authorize-security-group-ingress --group-id $SG_ID --region $REGION \
    --protocol tcp --port 443 --cidr "0.0.0.0/0"

# Allow port 8000 (direct access during setup)
aws ec2 authorize-security-group-ingress --group-id $SG_ID --region $REGION \
    --protocol tcp --port 8000 --cidr "0.0.0.0/0"

echo ""
echo "=== Step 2: Create Key Pair (if needed) ==="
aws ec2 create-key-pair \
    --key-name $KEY_NAME \
    --region $REGION \
    --query 'KeyMaterial' \
    --output text > ${KEY_NAME}.pem
chmod 400 ${KEY_NAME}.pem
echo "Key saved: ${KEY_NAME}.pem"

echo ""
echo "=== Step 3: Launch EC2 Instance ==="
INSTANCE_ID=$(aws ec2 run-instances \
    --image-id $AMI_ID \
    --instance-type $INSTANCE_TYPE \
    --key-name $KEY_NAME \
    --security-group-ids $SG_ID \
    --region $REGION \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":20,"VolumeType":"gp3"}}]' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=qa-agent}]" \
    --output text --query 'Instances[0].InstanceId')
echo "Instance: $INSTANCE_ID"

echo ""
echo "=== Step 4: Wait for running... ==="
aws ec2 wait instance-running --instance-ids $INSTANCE_ID --region $REGION

PUBLIC_IP=$(aws ec2 describe-instances \
    --instance-ids $INSTANCE_ID \
    --region $REGION \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text)
echo "Public IP: $PUBLIC_IP"

echo ""
echo "=== Step 5: Connect and deploy ==="
echo ""
echo "  SSH into the instance:"
echo "    ssh -i ${KEY_NAME}.pem ubuntu@$PUBLIC_IP"
echo ""
echo "  Then run the setup script:"
echo "    curl -sSL https://raw.githubusercontent.com/Arun-Engineer/qa-agent/main/deploy/ec2_setup.sh | bash"
echo ""
echo "  Or copy and run manually:"
echo "    scp -i ${KEY_NAME}.pem deploy/ec2_setup.sh ubuntu@$PUBLIC_IP:~/"
echo "    ssh -i ${KEY_NAME}.pem ubuntu@$PUBLIC_IP 'bash ec2_setup.sh'"
echo ""
echo "  Access the app:"
echo "    http://$PUBLIC_IP:8000"
echo ""
