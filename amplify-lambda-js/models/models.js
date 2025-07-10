//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

/**
 * Ensures numeric model properties are converted to numbers instead of strings
 * @param {Object} model - The model object to sanitize
 * @returns {Object} - The model with numeric properties converted to numbers
 */
const ensureNumericProperties = (model) => {
    if (!model) return model;
    
    const numericFields = [
        'inputContextWindow',
        'outputTokenLimit',
        'inputTokenCost',
        'outputTokenCost', 
        'cachedTokenCost'
    ];
    
    const sanitizedModel = { ...model };
    
    numericFields.forEach(field => {
        if (sanitizedModel[field] !== undefined && sanitizedModel[field] !== null) {
            const numValue = Number(sanitizedModel[field]);
            if (!isNaN(numValue)) {
                sanitizedModel[field] = numValue;
            }
        }
    });
    
    return sanitizedModel;
};

export const getUserAvailableModels = async (accessToken) => {
    const apiUrl = process.env.API_BASE_URL + '/available_models'; 
    
    const response = await fetch(apiUrl, {
        method: "GET",
        headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer "+accessToken
        },
    });

    if (!response.ok) {
        console.error("Error fetching ops: ", response.statusText);
        return [];
    }

    const data = await response.json();

    if(!data || !data.success || !data.data || !data.data.models) {
        console.error("Missing data in user available models response: ", response.statusText);
        return [];
    }

    const modelsMap = data.data.models.reduce((acc, model) => {
        acc[model.id] = ensureNumericProperties(model); // Use the model's `id` as the key
        return acc;
    }, {});

    const model_data = {...data.data, models: modelsMap};

    // Apply ensureNumericProperties to all model_data properties except 'models'
    for (const key in model_data) {
        if (key !== 'models' && model_data[key]) {
            try {
                model_data[key] = ensureNumericProperties(model_data[key]);
            } catch (error) {
               console.error("Error ensuring numeric properties for ", model_data[key], "\nError: ", error);
            }
        }
    }

    // if default is null, we will override it with the user chosen model in router.
    if (!model_data.advanced) model_data.advanced = model_data.default;
    if (!model_data.cheapest) model_data.cheapest = model_data.default;
    if (!model_data.documentCaching) model_data.documentCaching = model_data.cheapest;

    return model_data;
}

