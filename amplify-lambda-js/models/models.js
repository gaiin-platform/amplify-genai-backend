
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
        acc[model.id] = model; // Use the model's `id` as the key
        return acc;
    }, {});

    const model_data = {...data.data, models: modelsMap};

    // if default is null, we will override it with the user chosen model in router.
    if (!model_data.advanced) model_data.advanced = model_data.default;
    if (!model_data.cheapest) model_data.cheapest = model_data.default;

    return model_data;
}

