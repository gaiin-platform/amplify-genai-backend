
// for all Claude models 
export const claudeTransform = (event) => {
    if(event && event.d && event.d.delta && event.d.delta.text) {
        return {d: event.d.delta.text}
    }
    else {
        return null;
    }
}


// for Mistral 7b and Mixtral 7x8b
export const mistralTransform = (event) => { 
    if(event && event.d) { 
        return event
    }
    else {
        return null;
    }
}


export const bedrockConverseTransform = (event) => { 
    console.log(event)
    if (event && event.d && event.d.delta && event.d.delta.text) { 
        return {d: event.d.delta.text}
    } else {
        return null;
    }
}