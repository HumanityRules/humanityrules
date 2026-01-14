#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { PipelineStack } from "../lib/pipeline/pipeline-stack";
import { Account, Region } from "../constants/config";

const app = new cdk.App();

/**
 * TODO : Create different pipeline for different branches
 */

new PipelineStack(app, "DIPPipelineStackId", {
    stackName: "DIPPipelineStack",
    env: {
        account: Account.PIPELINE,
        region: Region.US_EAST_1,
    },
});
