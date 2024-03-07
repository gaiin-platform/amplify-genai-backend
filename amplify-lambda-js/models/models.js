export const ModelID = {
    GPT_4_TURBO_AZ: 'gpt-4-1106-Preview',
    GPT_4_TURBO: 'gpt-4-1106-preview',
    GPT_3_5: 'gpt-3.5-turbo',
    GPT_3_5_FN: 'gpt-3.5-turbo-1106',
    GPT_3_5_AZ: 'gpt-35-turbo',
    GPT_3_5_AZ_FN: 'gpt-35-turbo-1106',
    CLAUDE_INSTANT_1_2: 'anthropic.claude-instant-v1',
    CLAUDE_2_1: 'anthropic.claude-v2:1',
    CLAUDE_3_SONNET: 'anthropic.claude-3-sonnet-20240229-v1:0',
    MISTRAL_7B: 'mistral.mistral-7b-instruct-v0:2',
    MIXTRAL_8X7B: 'mistral.mixtral-8x7b-instruct-v0:1'

};


export const Models = {
    [ModelID.GPT_4_TURBO_AZ]: {
        id: ModelID.GPT_4_TURBO,
        name: 'GPT-4-Turbo (Azure)',
        tokenLimit: 120000,
        visible: true,
    },
    [ModelID.GPT_4_TURBO]: {
        id: ModelID.GPT_4_TURBO,
        name: 'GPT-4-Turbo',
        tokenLimit: 120000,
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
    [ModelID.CLAUDE_INSTANT_1_2]: {
        id: ModelID.CLAUDE_INSTANT_1_2,
        name: 'Claude-Instant-1.2 (bedrock)',
        tokenLimit: 100000,
        visible: false,
    },
    [ModelID.CLAUDE_2_1]: {
        id: ModelID.CLAUDE_2_1,
        name: 'Claude-2.1 (bedrock)',
        tokenLimit: 200000,
        visible: false,
    },
    [ModelID.CLAUDE_3_SONNET]: {
     id: ModelID.CLAUDE_3_SONNET,
     name: 'Claude-3-Sonnet (bedrock)',
     tokenLimit: 200000,
     visible: false,
    },
    [ModelID.MISTRAL_7B]: {
        id: ModelID.MISTRAL_7B,
        name: 'Mistral-7b-Instruct (bedrock)',
        tokenLimit: 8000,
        visible: false,
       },
    [ModelID.MIXTRAL_8X7B]: {
        id: ModelID.MIXTRAL_8X7B,
        name: 'Mixtral-7x8b-Instruct (bedrock)',
        tokenLimit: 32000,
        visible: false,
       },
};


