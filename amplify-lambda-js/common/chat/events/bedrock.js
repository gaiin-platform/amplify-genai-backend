//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

// for all Claude models  Not In Use
export const claudeTransform = (event) => {
    if(event && event.d && event.d.delta && event.d.delta.text) {
        return {d: event.d.delta.text}
    }
    else {
        return null;
    }
}


// for Mistral 7b and Mixtral 7x8b    Not In Use
export const mistralTransform = (event) => { 
    if(event && event.d) { 
        return event
    }
    else {
        return null;
    }
}


export const bedrockConverseTransform = (event) => { 
    if (event && event.d && event.d.delta && event.d.delta.text) { 
        return {d: event.d.delta.text}
    } else {
        return null;
    }
}

export const bedrockTokenUsageTransform = (event) => {
    if (event && event.d && event.d.usage) {
        return event.d.usage;
    } else {
        return null;
    }
}