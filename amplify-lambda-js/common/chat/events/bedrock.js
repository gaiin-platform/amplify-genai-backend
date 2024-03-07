
// for claude 2.1 and claude Instant 1.2
export const claudeTransform = (event) => { 
    if(event && event.d && event.d.completion) {
        return {d: event.d.completion}
    }
    else {
        return null;
    }
}

// for Claude 3 Sonnet 
export const claudeSonnetTransform = (event) => {
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