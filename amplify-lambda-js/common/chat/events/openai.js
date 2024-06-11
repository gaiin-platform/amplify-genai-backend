//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas



export const transform = (event) => {
    if(event && event.choices && event.choices.length > 0 && event.choices[0].delta){
        if(event.choices[0].delta.tool_calls){
            const calls = event.choices[0].delta.tool_calls;
            return {d: {tool_calls:calls}};
        }
        else if(event.choices[0].delta.content) {
            return {d: event.choices[0].delta.content};
        }
    }
    else {
        return null;
    }
}