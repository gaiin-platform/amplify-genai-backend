//Copyright (c) 2024 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import axios from 'axios';
import { getSecretApiKey } from './secrets.js';
import { getLogger } from './logging.js';
import { v4 as uuidv4 } from 'uuid';

const logger = getLogger("dalle");

const DALLE_API_URL = "https://api.openai.com/v1/images/generations";

/**
 * Generate an image using DALL-E 3
 * @param {Object} request - The image generation request
 * @param {string} request.prompt - The image description prompt
 * @param {string} request.size - Image size: "1024x1024", "1024x1792", or "1792x1024"
 * @param {string} request.quality - Image quality: "standard" or "hd"
 * @param {string} request.style - Image style: "vivid" or "natural"
 * @returns {Promise<Object>} The generation response
 */
export const generateImage = async (request) => {
    try {
        const apiKey = await getSecretApiKey("OPENAI_API_KEY");

        if (!apiKey) {
            logger.error("OPENAI_API_KEY not configured in secrets");
            return {
                success: false,
                error: "Image generation service not configured"
            };
        }

        const { prompt, size = "1024x1024", quality = "standard", style = "vivid" } = request;

        if (!prompt || typeof prompt !== 'string' || prompt.trim().length === 0) {
            return {
                success: false,
                error: "A valid prompt is required"
            };
        }

        logger.info(`Generating DALL-E 3 image: size=${size}, quality=${quality}, style=${style}`);

        const response = await axios.post(
            DALLE_API_URL,
            {
                model: "dall-e-3",
                prompt: prompt.trim(),
                n: 1,
                size: size,
                quality: quality,
                style: style,
                response_format: "url"
            },
            {
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${apiKey}`
                },
                timeout: 120000 // 2 minute timeout for image generation
            }
        );

        if (response.data && response.data.data && response.data.data.length > 0) {
            const imageData = response.data.data[0];

            return {
                success: true,
                image: {
                    id: uuidv4(),
                    prompt: prompt,
                    revisedPrompt: imageData.revised_prompt,
                    url: imageData.url,
                    size: size,
                    quality: quality,
                    style: style,
                    createdAt: new Date().toISOString()
                }
            };
        }

        return {
            success: false,
            error: "No image was generated"
        };

    } catch (error) {
        logger.error("Error generating DALL-E image:", error);

        if (error.response) {
            const status = error.response.status;
            const errorData = error.response.data;

            if (status === 400 && errorData?.error?.code === 'content_policy_violation') {
                return {
                    success: false,
                    error: "Your request was rejected due to content policy. Please modify your prompt and try again."
                };
            }

            if (status === 429) {
                return {
                    success: false,
                    error: "Rate limit exceeded. Please try again in a moment."
                };
            }

            if (status === 401) {
                return {
                    success: false,
                    error: "Image generation service authentication failed"
                };
            }

            return {
                success: false,
                error: errorData?.error?.message || `Request failed with status ${status}`
            };
        }

        if (error.code === 'ECONNABORTED') {
            return {
                success: false,
                error: "Image generation timed out. Please try again."
            };
        }

        return {
            success: false,
            error: "Failed to generate image. Please try again."
        };
    }
};
