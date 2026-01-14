export interface EnvironmentConfig {
    stageName: Stage;
    env: {
        account: Account;
        region: Region;
    };
}

export const enum Region {
    AP_SOUTH_1 = "ap-south-1",
    EU_WEST_1 = "eu-west-1",
    US_EAST_1 = "us-east-1",
}

export const enum Stage {
    STAGE = "stage",
    PROD = "prod",
}

export const enum Account {
    PIPELINE = "156041420396",

    STAGE_AP_SOUTH_1 = "156041420396",
    STAGE_EU_WEST_1 = "156041420396",

    PROD_AP_SOUTH_1 = "156041420396",
    PROD_EU_WEST_1 = "156041420396",
}
