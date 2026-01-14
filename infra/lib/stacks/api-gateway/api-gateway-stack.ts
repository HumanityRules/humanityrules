import * as cdk from "aws-cdk-lib";
import {
    EndpointType,
    LambdaIntegration,
    MethodLoggingLevel,
    RestApi,
} from "aws-cdk-lib/aws-apigateway";
import { Function } from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import { EnvironmentConfig } from "../../../constants/config";

export interface ApiGatewayStackProps extends cdk.StackProps {
    envConfig: EnvironmentConfig;
    resources: {
        rtpPreprocessorLambda: Function;
    };
}

export class ApiGatewayStack extends cdk.Stack {
    constructor(scope: Construct, id: string, props: ApiGatewayStackProps) {
        super(scope, id, {
            stackName: `${props.envConfig.stageName}-${props.envConfig.env.region}-ApiGatewayStack`,
            ...props.envConfig,
        });

        const restApi = new RestApi(this, "ApiGatewayId", {
            restApiName: `${props.envConfig.stageName}-ApiGateway`,
            description: `API Gateway for apis in Distributed Image Processor. Stage : ${props.envConfig.stageName}, Region : ${props.envConfig.env.region}`,
            deploy: true,
            deployOptions: {
                stageName: props.envConfig.stageName,
                loggingLevel: MethodLoggingLevel.INFO,
                metricsEnabled: true,
            },
            cloudWatchRole: true,
            endpointConfiguration: {
                types: [EndpointType.REGIONAL],
            },
        });

        const rtpResource = restApi.root.addResource("rtp");
        rtpResource
            .addResource("process-file")
            .addMethod("POST", new LambdaIntegration(props.resources.rtpPreprocessorLambda), {
                methodResponses: [{ statusCode: "200" }],
            });
    }
}
