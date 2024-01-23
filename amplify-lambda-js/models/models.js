export const ModelID = {
    GPT_4_TURBO_AZ: 'gpt-4-1106-Preview',
    GPT_4_TURBO: 'gpt-4-1106-preview',
    GPT_3_5: 'gpt-3.5-turbo',
    GPT_3_5_FN: 'gpt-3.5-turbo-1106',
    GPT_3_5_AZ: 'gpt-35-turbo',
    GPT_3_5_AZ_FN: 'gpt-35-turbo-1106',
};

export const Models = {
    [ModelID.GPT_4_TURBO_AZ]: {
        id: ModelID.GPT_4_TURBO,
        name: 'GPT-4-Turbo (Azure)',
        tokenLimit: 128000,
        visible: true,
    },
    [ModelID.GPT_4_TURBO]: {
        id: ModelID.GPT_4_TURBO,
        name: 'GPT-4-Turbo',
        tokenLimit: 128000,
        visible: true,
    },
    [ModelID.GPT_3_5]: {
        id: ModelID.GPT_3_5,
        name: 'GPT-3.5',
        tokenLimit: 4000,
        visible: true,
    },
    [ModelID.GPT_3_5_FN]: {
        id: ModelID.GPT_3_5_FN,
        name: 'GPT-3.5 Function Calling',
        tokenLimit: 4000,
        visible: false,
    },
    [ModelID.GPT_3_5_AZ]: {
        id: ModelID.GPT_3_5_AZ,
        name: 'GPT-3.5 (Azure)',
        tokenLimit: 4000,
        visible: false,
    },
};