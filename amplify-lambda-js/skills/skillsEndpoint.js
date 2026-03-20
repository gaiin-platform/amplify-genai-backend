//Copyright (c) 2026 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas, Jagadeesh Reddy Vanga, Tina Seviert

import { getLogger } from "../common/logging.js";
import * as skillsService from "./skillsService.js";

const logger = getLogger("skills-endpoint");

/**
 * Handle skills API requests
 * @param {Object} params - Request parameters including user info
 * @param {Object} request - The skills request body
 * @returns {Object} Response data
 */
export const handleSkillsRequest = async (params, request) => {
    const { action, data } = request;
    const user = params.user || params.account?.user;

    if (!user) {
        throw new Error("User not authenticated");
    }

    logger.info(`Skills request: ${action} by user ${user}`);

    try {
        switch (action) {
            case "create": {
                // Screen skill if being made public
                if (data.isPublic) {
                    logger.info(`Screening skill "${data.name}" before making public`);
                    const screening = await skillsService.screenSkillForPublic(params, data);

                    if (!screening.approved) {
                        return {
                            success: false,
                            message: `Skill cannot be made public: ${screening.reason}`,
                            screening: {
                                approved: false,
                                reason: screening.reason,
                                category: screening.category
                            }
                        };
                    }
                }

                return {
                    success: true,
                    data: await skillsService.createSkill(user, data)
                };
            }

            case "list":
                return {
                    success: true,
                    data: await skillsService.getUserSkills(user, data?.includeShared ?? true)
                };

            case "get": {
                if (!data?.skillId) {
                    throw new Error("skillId is required");
                }
                const skill = await skillsService.getSkillById(data.skillId, user);
                if (!skill) {
                    return {
                        success: false,
                        message: "Skill not found or access denied"
                    };
                }
                return {
                    success: true,
                    data: skill
                };
            }

            case "update": {
                if (!data?.skillId) {
                    throw new Error("skillId is required");
                }

                const updates = data.updates || data;

                // Screen skill if being made public (check if isPublic is being set to true)
                if (updates.isPublic === true) {
                    // Get existing skill to check if it was already public
                    const existingSkill = await skillsService.getSkillById(data.skillId, user);

                    // Only screen if changing from private to public
                    if (existingSkill && !existingSkill.isPublic) {
                        // Merge existing skill data with updates for complete screening
                        const skillToScreen = {
                            ...existingSkill,
                            ...updates
                        };

                        logger.info(`Screening skill "${skillToScreen.name}" before making public`);
                        const screening = await skillsService.screenSkillForPublic(params, skillToScreen);

                        if (!screening.approved) {
                            return {
                                success: false,
                                message: `Skill cannot be made public: ${screening.reason}`,
                                screening: {
                                    approved: false,
                                    reason: screening.reason,
                                    category: screening.category
                                }
                            };
                        }
                    }
                }

                return {
                    success: true,
                    data: await skillsService.updateSkill(user, data.skillId, updates)
                };
            }

            case "delete":
                if (!data?.skillId) {
                    throw new Error("skillId is required");
                }
                return {
                    success: true,
                    data: await skillsService.deleteSkill(user, data.skillId)
                };

            case "share":
                if (!data?.skillId || !data?.shareConfig) {
                    throw new Error("skillId and shareConfig are required");
                }
                return {
                    success: true,
                    data: await skillsService.shareSkill(user, data.skillId, data.shareConfig)
                };

            case "unshare":
                if (!data?.shareId) {
                    throw new Error("shareId is required");
                }
                // TODO: Implement unshare
                return {
                    success: true,
                    message: "Share removed"
                };

            case "autoSelect":
                if (!data?.message) {
                    throw new Error("message is required for auto-select");
                }
                return {
                    success: true,
                    data: await skillsService.autoSelectSkills(user, data.message, data.context || {})
                };

            case "getPublic":
                return {
                    success: true,
                    data: await skillsService.getPublicSkills(data?.limit || 50, data?.tags)
                };

            case "incrementUsage":
                if (!data?.skillId) {
                    throw new Error("skillId is required");
                }
                await skillsService.incrementSkillUsage(data.skillId);
                return {
                    success: true
                };

            default:
                throw new Error(`Unknown skills action: ${action}`);
        }
    } catch (error) {
        logger.error(`Skills request failed for action ${action}:`, error);
        return {
            success: false,
            message: error.message || "Skills operation failed"
        };
    }
};
