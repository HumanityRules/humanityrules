import { Account, Region, Stage, EnvironmentConfig } from "../../constants/config";

export interface PipelineEnvironmentsConfig {
    [Stage.STAGE]: {
        [Region.AP_SOUTH_1]: EnvironmentConfig;
        [Region.EU_WEST_1]: EnvironmentConfig;
    };
    [Stage.PROD]: {
        [Region.AP_SOUTH_1]: EnvironmentConfig;
        [Region.EU_WEST_1]: EnvironmentConfig;
    };
}

export const pipelineEnvironments: PipelineEnvironmentsConfig = {
    [Stage.STAGE]: {
        [Region.AP_SOUTH_1]: {
            stageName: Stage.STAGE,
            env: {
                account: Account.STAGE_AP_SOUTH_1,
                region: Region.AP_SOUTH_1,
            },
        },
        [Region.EU_WEST_1]: {
            stageName: Stage.STAGE,
            env: {
                account: Account.STAGE_EU_WEST_1,
                region: Region.EU_WEST_1,
            },
        },
    },
    [Stage.PROD]: {
        [Region.AP_SOUTH_1]: {
            stageName: Stage.PROD,
            env: {
                account: Account.PROD_AP_SOUTH_1,
                region: Region.AP_SOUTH_1,
            },
        },
        [Region.EU_WEST_1]: {
            stageName: Stage.PROD,
            env: {
                account: Account.PROD_EU_WEST_1,
                region: Region.EU_WEST_1,
            },
        },
    },
};
