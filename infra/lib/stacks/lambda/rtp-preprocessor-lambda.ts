import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { Function, Runtime, Code } from "aws-cdk-lib/aws-lambda";
import { EnvironmentConfig } from "../../../constants/config";

export class RTPPreprocessorLambdaStack extends cdk.Stack {
    private readonly lambdaFunction: Function;

    private readonly FUNCTION_NAME = "rtp-preprocessor-lambda";
    private readonly HANDLER = "com.dip.RTPPreprocessorLambda.RTPPreprocessorLambda::handleRequest";
    private readonly MEMORY_SIZE_MB = 512;
    private readonly TIMEOUT_S = cdk.Duration.seconds(30);
    private readonly CODE_PATH =
        "../software/rtp-preprocessor-lambda/build/libs/lambda-uberjar.jar";

    constructor(scope: Construct, id: string, envConfig: EnvironmentConfig) {
        super(scope, id, {
            stackName: `${envConfig.stageName}-${envConfig.env.region}-RTPPreprocessorLambdaStack`,
            ...envConfig,
        });

        this.lambdaFunction = new Function(this, "RTPPreprocessorLambdaId", {
            handler: this.HANDLER,
            runtime: Runtime.JAVA_21,
            code: Code.fromAsset(this.CODE_PATH),
            memorySize: this.MEMORY_SIZE_MB,
            timeout: this.TIMEOUT_S,
            functionName: this.FUNCTION_NAME,
        });
    }

    public getLambdaFunction(): Function {
        return this.lambdaFunction;
    }
}
