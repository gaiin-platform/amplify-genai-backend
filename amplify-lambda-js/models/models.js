

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
    CLAUDE_3_HAIKU: 'anthropic.claude-3-haiku-20240307-v1:0',
    CLAUDE_3_OPUS: 'anthropic.claude-3-opus-20240229-v1:0',
    MISTRAL_7B: 'mistral.mistral-7b-instruct-v0:2',
    MIXTRAL_8X7B: 'mistral.mixtral-8x7b-instruct-v0:1',
    MISTRAL_LARGE: 'mistral.mistral-large-2402-v1:0'
};


export const Models = {
    [ModelID.GPT_4_TURBO_AZ]: {
        id: ModelID.GPT_4_TURBO,
        name: 'GPT-4-Turbo (Azure)',
        tokenLimit: 120000,
        visible: true,
        outputCost: .03,
        inputCost: .01,
    },
    [ModelID.GPT_4_TURBO]: {
        id: ModelID.GPT_4_TURBO,
        name: 'GPT-4-Turbo',
        tokenLimit: 120000,
        visible: true,
        outputCost: .03,
        inputCost: .01,
    },
    [ModelID.GPT_3_5]: {
        id: ModelID.GPT_3_5,
        name: 'GPT-3.5',
        tokenLimit: 4000,
        visible: true,
        outputCost: .002,
        inputCost: .001,
    },
    [ModelID.GPT_3_5_FN]: {
        id: ModelID.GPT_3_5_FN,
        name: 'GPT-3.5 Function Calling',
        tokenLimit: 4000,
        visible: false,
        outputCost: .002,
        inputCost: .001,
    },
    [ModelID.GPT_3_5_AZ]: {
        id: ModelID.GPT_3_5_AZ,
        name: 'GPT-3.5 (Azure)',
        tokenLimit: 4000,
        visible: false,
        outputCost: .002,
        inputCost: .001,
    },
    [ModelID.CLAUDE_INSTANT_1_2]: {
        id: ModelID.CLAUDE_INSTANT_1_2,
        name: 'Claude-Instant-1.2 (bedrock)',
        tokenLimit: 100000,
        visible: false,
        outputCost: .0024,
        inputCost: .0008,
    },
    [ModelID.CLAUDE_2_1]: {
        id: ModelID.CLAUDE_2_1,
        name: 'Claude-2.1 (bedrock)',
        tokenLimit: 200000,
        visible: false,
        outputCost: .024,
        inputCost: .008,
    },
    [ModelID.CLAUDE_3_SONNET]: {
        id: ModelID.CLAUDE_3_SONNET,
        name: 'Claude-3-Sonnet (bedrock)',
        tokenLimit: 200000,
        visible: false,
        outputCost: 0.01500,
        inputCost: 0.00300,
    },
    [ModelID.CLAUDE_3_HAIKU]: {
        id: ModelID.CLAUDE_3_HAIKU,
        name: 'Claude-3-Haiku (bedrock)',
        tokenLimit: 200000,
        visible: false,
        outputCost: 0.00125,
        inputCost: 0.00025,
    },
    [ModelID.CLAUDE_3_OPUS]: {
        id: ModelID.CLAUDE_3_OPUS,
        name: 'Claude-3-OPUS (bedrock)',
        tokenLimit: 200000,
        visible: false,
        outputCost: 0.07500,
        inputCost: 0.01500,
        },
    [ModelID.MISTRAL_7B]: {
        id: ModelID.MISTRAL_7B,
        name: 'Mistral-7b-Instruct (bedrock)',
        tokenLimit: 8000,
        visible: false,
        outputCost: 0.00015,
        inputCost: 0.0002,
        },
    [ModelID.MIXTRAL_8X7B]: {
        id: ModelID.MIXTRAL_8X7B,
        name: 'Mixtral-7x8b-Instruct (bedrock)',
        tokenLimit: 32000,
        visible: false,
        outputCost: 0.00045,
        inputCost: 0.0007,
    },

    [ModelID.MISTRAL_LARGE]: {
        id: ModelID.MISTRAL_LARGE,
        name: 'Mistral-Large (bedrock)',
        tokenLimit: 4000,
        visible: false,
        outputCost: 0.024,
        inputCost: 0.008,
      },
};


