import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { EnvironmentConfig } from "../../../constants/config";

export class VpcStack extends cdk.Stack {
    constructor(scope: Construct, id: string, envConfig: EnvironmentConfig) {
        super(scope, id, {
            stackName: `${envConfig.stageName}-${envConfig.env.region}-VpcStack`,
            ...envConfig,
        });

        const vpc = new ec2.Vpc(this, "DIPVpcId", {
            vpcName: `${envConfig.stageName}-${envConfig.env.region}-Vpc`,
            maxAzs: 2,
            subnetConfiguration: [
                {
                    cidrMask: 24,
                    name: "public-subnet",
                    subnetType: ec2.SubnetType.PUBLIC,
                },
                {
                    cidrMask: 24,
                    name: "private-subnet",
                    subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
                },
            ],
        });
    }
}
