import * as cdk from "aws-cdk-lib";

import { Construct } from "constructs";
import { EnvironmentConfig, Region, Stage } from "../../../constants/config";
import { pipelineEnvironments } from "../pipeline-config";

/**
 * Ideally stacks in all evironments should be same i.e. dev/test should be similar to prod
 * Incase we want to exclude some stack in a particular stage, use if/else checks
 * OR you can even have separate stage file for each environment
 */

export class ProdStage extends cdk.Stage {
    constructor(scope: Construct, id: string) {
        super(scope, id);

        this.prodApSouth1Stacks(pipelineEnvironments[Stage.PROD][Region.AP_SOUTH_1]);
        this.prodEuWest1Stacks(pipelineEnvironments[Stage.PROD][Region.EU_WEST_1]);
    }

    private prodApSouth1Stacks(props: EnvironmentConfig): void {}
    private prodEuWest1Stacks(props: EnvironmentConfig): void {}
}
