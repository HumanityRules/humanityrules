import * as cdk from "aws-cdk-lib";
import { CodePipeline, CodePipelineSource, ShellStep } from "aws-cdk-lib/pipelines";
import { Construct } from "constructs";
import { Stage } from "../../constants/config";
import { ProdStage } from "./stages/prod-stage";
import { StagingStage } from "./stages/staging-stage";

export class PipelineStack extends cdk.Stack {
    constructor(scope: Construct, id: string, props: cdk.StackProps) {
        super(scope, id, props);

        /**
         * TODO : we shoule create our pipeline for the given branch and repository,
         * DIPPipeline-PROD : pipeline should have all stages, triggered through main branch
         * DIPPipeline-STAGE : pipeline should have only dev/stage stage, triggered through stage/dev branch
         */

        // ====================================== MAIN Pipeline ======================================
        const pipeline = new CodePipeline(this, "DIPPipelineMainId", {
            pipelineName: "DIPPipeline-main",
            synth: new ShellStep("Synth", {
                input: CodePipelineSource.gitHub("anuragsati/distributed-image-processor", "main"),
                commands: [
                    // Infra deployment
                    "cd infra",
                    "npm ci",
                    "npm run build",
                    "npx cdk synth",
                ],
                primaryOutputDirectory: "infra/cdk.out",
            }),
            crossAccountKeys: false,
            selfMutation: true,
        });

        pipeline.addStage(new StagingStage(this, Stage.STAGE));
        // pipeline.addStage(new ProdStage(this, Stage.PROD));
    }
}
