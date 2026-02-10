# ecs_cdk_stack.py
from aws_cdk import (
    Stack, aws_ecs as ecs, aws_ec2 as ec2, aws_ecr as ecr,
    aws_ecs_patterns as ecs_patterns, aws_iam as iam
)
from constructs import Construct

class QaAgentEcsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        vpc = ec2.Vpc(self, "QaAgentVPC", max_azs=2)

        cluster = ecs.Cluster(self, "QaAgentCluster", vpc=vpc)

        repo = ecr.Repository.from_repository_name(self, "QaAgentRepo", "qa-agent-demo")

        task_def = ecs.FargateTaskDefinition(self, "QaAgentTaskDef")
        task_def.add_container(
            "QaAgentContainer",
            image=ecs.ContainerImage.from_ecr_repository(repo),
            logging=ecs.LogDriver.aws_logs(stream_prefix="QaAgent"),
            environment={"ENV": "prod"},
            port_mappings=[ecs.PortMapping(container_port=8000)]
        )

        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self, "QaAgentService",
            cluster=cluster,
            task_definition=task_def,
            desired_count=2,
            public_load_balancer=True,
        )
