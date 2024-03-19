import tiktokenModel from '@dqbd/tiktoken/encoders/cl100k_base.json' assert {type: 'json'};
import { Tiktoken } from '@dqbd/tiktoken/lite';


export const createTokenCounter = (model) => {

    // We only do OpenAI for now, so we ignore the model

    const encoding = new Tiktoken(
        tiktokenModel.bpe_ranks,
        tiktokenModel.special_tokens,
        tiktokenModel.pat_str,
    );

    return {
        countTokens: (text) => {
            const tokens = encoding.encode(text);
            return tokens.length;
        },
        countMessageTokens: (messages) => {
            const counts = messages.map(m => encoding.encode(m.content).length);
            const count = counts.reduce((accumulator, currentValue) => accumulator + currentValue, 0);
            return count;
        },
        free: () => {
            encoding.free();
        }
    };
}

export const countTokens = (text) => {
    const encoding = new Tiktoken(
        tiktokenModel.bpe_ranks,
        tiktokenModel.special_tokens,
        tiktokenModel.pat_str,
    );

    const tokens = encoding.encode(text);

    encoding.free();

    return tokens.length;
}

export const countChatTokens = (messages) => {
    const encoding = new Tiktoken(
        tiktokenModel.bpe_ranks,
        tiktokenModel.special_tokens,
        tiktokenModel.pat_str,
    );

    const counts = messages.map(m => encoding.encode(m.content).length);
    const count = counts.reduce((accumulator, currentValue) => accumulator + currentValue, 0);

    encoding.free();

    return count;
}